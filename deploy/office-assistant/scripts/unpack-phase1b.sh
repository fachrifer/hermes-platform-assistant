#!/usr/bin/env bash
set -euo pipefail

# Unpack the Phase 1b tree shipped by ship-phase1b.ps1 (run on the Lab VM).
#   bash ~/hermes-assistant/images/unpack-phase1b.sh
# Stop the old stack first (docker compose down --remove-orphans).
# Keeps env files, certs and the gateway-owned route files.

DIR="${OFFICE_VM_DIR:-$HOME/hermes-assistant}"
IMAGES="$DIR/images"
TS="$(date +%Y%m%d-%H%M%S)"
RUNTIME_FILES=(edge/edge-routes edge/traefik-dynamic/routes.yml edge/edge-locations.conf)
RETIRED=(
  scripts/office-gw-fast.sh scripts/office-gw-get.sh scripts/office-gw-propose.sh scripts/office-gw-watch.sh
  scripts/office-watch-eval.py scripts/office-watch-kanban.py scripts/office-watch-summary.sh
  scripts/office-watch-sync.sh scripts/s6-office-watch-run scripts/s6-office-watch-sync-run
  scripts/patch-hermes-airgap-provider.py scripts/hermes-airgap-provider-cont-init.sh
  scripts/apply-office-llm-config.py scripts/hermes-office-llm-cont-init.sh scripts/hermes-supervisor-cron-init.sh
  hermes/config.yaml hermes/.env.example hermes/office-gateway.openapi.json
  hermes/supervisor/cron.yaml hermes/supervisor/cron.json
  skills/edge skills/llm-edge skills/office-platform
)

cd "$DIR"
for f in fleet-p1b-tree.tgz fleet-p1b-gateway.tgz; do
  [[ -f "$IMAGES/$f" ]] || { echo "error: $IMAGES/$f missing; run ship-phase1b.ps1 first" >&2; exit 1; }
done
if tar -xzOf "$IMAGES/fleet-p1b-tree.tgz" scripts/lib.sh | grep -q $'\r'; then
  echo "error: fleet-p1b-tree.tgz has CRLF line endings; re-ship from the PC" >&2
  exit 1
fi
if [[ -n "$(docker compose ps -q 2>/dev/null)" ]]; then
  echo "error: fleet containers still running; run: docker compose down --remove-orphans" >&2
  exit 1
fi

backup="$HOME/hermes-assistant-pre-phase1b-$TS.tgz"
tar -czf "$backup" --exclude=./images -C "$DIR" .
chmod 600 "$backup"
echo "backup: $backup"

stash="$DIR/.retired-pre-phase1b-$TS"
for path in "${RETIRED[@]}"; do
  if [[ -e "$path" ]]; then
    mkdir -p "$stash/$(dirname "$path")"
    mv "$path" "$stash/$path"
    echo "retired $path"
  fi
done
if [[ -d office_gateway ]]; then
  mkdir -p "$stash"
  mv office_gateway "$stash/office_gateway"
fi

excludes=()
for path in "${RUNTIME_FILES[@]}"; do
  if [[ -e "$path" ]]; then
    excludes+=("--exclude=$path")
  fi
done
tar -xzf "$IMAGES/fleet-p1b-tree.tgz" "${excludes[@]}"
tar -xzf "$IMAGES/fleet-p1b-gateway.tgz"
chmod +x scripts/*.sh
echo "unpacked tree (kept live: ${RUNTIME_FILES[*]})"

if [[ -f "$IMAGES/fleet-p1b-images.tar" ]]; then
  docker load -i "$IMAGES/fleet-p1b-images.tar"
fi
echo "next: ./scripts/migrate-env-phase1b.sh"
