#!/usr/bin/env bash
set -euo pipefail

# Run on a networked laptop. Does not start Compose on the Lab itself.
# Packages images if needed, rsyncs the runnable tree + tarball, then SSHs
# and runs scripts/deploy-and-start.sh on the Lab VM.
#
# Lab layout (same as the existing stack home):
#   timai@10.216.4.80:/home/timai/hermes-assistant
#   ├── scripts/copy-env.sh deploy-and-start.sh load-and-start.sh stop.sh ...
#   ├── docker-compose.yml
#   ├── office_gateway/          (needed for compose --build)
#   └── Dockerfile.office-gateway
#
#   ./scripts/deploy.sh
#   OFFICE_DEPLOY_REBUILD=1 ./scripts/deploy.sh

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

OFFICE_DEPLOY_HOST="${OFFICE_DEPLOY_HOST:-10.216.4.80}"
OFFICE_DEPLOY_USER="${OFFICE_DEPLOY_USER:-timai}"
OFFICE_DEPLOY_DIR="${OFFICE_DEPLOY_DIR:-/home/timai/hermes-assistant}"
REMOTE="${OFFICE_DEPLOY_USER}@${OFFICE_DEPLOY_HOST}"
TARBALL="${1:-$DEFAULT_TARBALL}"
# macOS TMPDIR is too long for OpenSSH ControlPath (104-byte socket path limit).
CONTROL_PATH="/tmp/office-deploy-%C"
SSH_OPTS=(
  -o BatchMode=no
  -o PreferredAuthentications=keyboard-interactive,password,publickey
  -o NumberOfPasswordPrompts=3
  -o ControlMaster=auto
  -o "ControlPath=${CONTROL_PATH}"
  -o ControlPersist=600
  -o ConnectTimeout=15
)
SSH=(ssh "${SSH_OPTS[@]}")
export RSYNC_RSH="ssh ${SSH_OPTS[*]}"

cleanup_ssh() {
  ssh "${SSH_OPTS[@]}" -O exit "$REMOTE" >/dev/null 2>&1 || true
}
trap cleanup_ssh EXIT


# Scripts that must exist on the Lab VM to load images and run Compose.
LAB_SCRIPTS=(
  copy-env.sh
  deploy-and-start.sh
  load-and-start.sh
  stop.sh
  status.sh
  restart.sh
  doctor.sh
  save-images.sh
  lib.sh
  pair-env.sh
  deploy.sh
)

if [[ "${OFFICE_DEPLOY_REBUILD:-0}" == "1" || ! -f "$TARBALL" ]]; then
  echo "save images -> $TARBALL"
  "$SCRIPT_DIR/save-images.sh" "$TARBALL"
elif find "$REPO_ROOT/office_gateway" "$REPO_ROOT/Dockerfile.office-gateway" -newer "$TARBALL" | grep -q .; then
  echo "office_gateway newer than tarball; save images -> $TARBALL"
  "$SCRIPT_DIR/save-images.sh" "$TARBALL"
fi

if [[ ! -f "$TARBALL" ]]; then
  echo "error: missing $TARBALL" >&2
  exit 1
fi

for name in "${LAB_SCRIPTS[@]}"; do
  if [[ ! -e "$SCRIPT_DIR/$name" ]]; then
    echo "error: missing $SCRIPT_DIR/$name" >&2
    exit 1
  fi
done

cd "$REPO_ROOT"

echo "SSH login $REMOTE (enter password if prompted; /home/timai/hermes-assistant already exists)"
"${SSH[@]}" -tt "$REMOTE" true

echo "ensure images/ under existing $OFFICE_DEPLOY_DIR"
"${SSH[@]}" "$REMOTE" "mkdir -p '$OFFICE_DEPLOY_DIR/images'"

echo "rsync Compose tree + scripts -> $REMOTE:$OFFICE_DEPLOY_DIR"
rsync -az \
  --exclude '.env' \
  --exclude 'models.env' \
  --exclude 'hermes/*/.env' \
  --exclude '.kubeconfig.local' \
  --exclude '.local-login' \
  --exclude 'docker-compose.local.yml' \
  --exclude 'images/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  "$DEPLOY_DIR/" \
  "$REMOTE:$OFFICE_DEPLOY_DIR/"

echo "rsync office_gateway + Dockerfile.office-gateway -> $REMOTE:$OFFICE_DEPLOY_DIR"
rsync -az "$REPO_ROOT/office_gateway/" "$REMOTE:$OFFICE_DEPLOY_DIR/office_gateway/"
rsync -az "$REPO_ROOT/Dockerfile.office-gateway" "$REMOTE:$OFFICE_DEPLOY_DIR/Dockerfile.office-gateway"

if [[ ! -f "$DEPLOY_DIR/.env" ]]; then
  echo "error: missing $DEPLOY_DIR/.env (fill local credentials first)" >&2
  exit 1
fi

echo "pair office-gateway and A2A tokens across hermes role .env files"
"$SCRIPT_DIR/pair-env.sh"

# Overwrite Lab env with laptop files. mv through a temp name so a root-owned
# Lab .env can still be replaced when the directory is writable.
push_env() {
  local src="$1"
  local dest="$2"
  if [[ ! -f "$src" ]]; then
    return 0
  fi
  rsync -az "$src" "$REMOTE:${dest}.laptop-new"
  "${SSH[@]}" "$REMOTE" "mv -f '${dest}.laptop-new' '$dest'"
}

echo "replace Lab .env / models.env / hermes/*/.env from this laptop"
push_env "$DEPLOY_DIR/.env" "$OFFICE_DEPLOY_DIR/.env"
push_env "$DEPLOY_DIR/models.env" "$OFFICE_DEPLOY_DIR/models.env"
for role in $HERMES_ROLES; do
  push_env "$DEPLOY_DIR/hermes/$role/.env" "$OFFICE_DEPLOY_DIR/hermes/$role/.env"
done

echo "pin Lab bind addresses (laptop local-up uses 127.0.0.1)"
"${SSH[@]}" "$REMOTE" python3 - "$OFFICE_DEPLOY_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]) / ".env"
lab = dict(
    line.split("=", 1)
    for line in (
        "OFFICE_GATEWAY_CONTEXT=.",
        "HERMES_DASHBOARD_PUBLISH=10.216.4.80:9119",
        "HERMES_CONSOLE_PUBLISH=10.216.4.80:80",
        "OFFICE_KUBECONFIG=./kubeconfig.absent",
    )
)
lines = root.read_text().splitlines() if root.exists() else []
seen = set()
out = []
for line in lines:
    if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
        out.append(line)
        continue
    key, _current = line.split("=", 1)
    if key in lab:
        out.append(f"{key}={lab[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in lab.items():
    if key not in seen:
        out.append(f"{key}={value}")
root.write_text("\n".join(out) + "\n")
print("pinned Lab OFFICE_GATEWAY_CONTEXT / Dashboard / console / kubeconfig")
PY

echo "rsync office-fleet-images.tar.gz -> $REMOTE:$OFFICE_DEPLOY_DIR/images/"
rsync -az --progress "$TARBALL" "$REMOTE:$OFFICE_DEPLOY_DIR/images/office-fleet-images.tar.gz"

echo "chmod Lab VM scripts"
"${SSH[@]}" "$REMOTE" "chmod a+x '$OFFICE_DEPLOY_DIR'/scripts/*.sh"

echo "remote deploy-and-start on $REMOTE"
"${SSH[@]}" "$REMOTE" "cd '$OFFICE_DEPLOY_DIR' && ./scripts/deploy-and-start.sh"

echo "Dashboard: http://10.216.4.80:9119"
echo "Athena console: http://10.216.4.80"
echo "Remote status: ssh $REMOTE 'cd $OFFICE_DEPLOY_DIR && ./scripts/status.sh'"
