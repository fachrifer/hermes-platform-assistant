#!/usr/bin/env bash
set -euo pipefail

# Pin a shared dashboard session token and write .local-login for Hermes Desktop.
# Does not print the token. Does not start Compose.
#
#   ./scripts/desktop-connect.sh --lab     # http://10.216.4.80:9119  (default)
#   ./scripts/desktop-connect.sh --local   # http://127.0.0.1:9119

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

TARGET="lab"
for arg in "$@"; do
  case "$arg" in
    --lab) TARGET="lab" ;;
    --local) TARGET="local" ;;
    -h|--help)
      echo "usage: $0 [--lab|--local]"
      exit 0
      ;;
    *)
      echo "error: unknown argument $arg (use --lab or --local)" >&2
      exit 1
      ;;
  esac
done

cd "$DEPLOY_DIR"
"$SCRIPT_DIR/copy-env.sh"
"$SCRIPT_DIR/pair-env.sh"

python3 - "$DEPLOY_DIR" "$TARGET" <<'PY'
from pathlib import Path
import sys

deploy = Path(sys.argv[1])
target = sys.argv[2]
path = deploy / ".local-login"
kv: dict[str, str] = {}
if path.exists():
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        kv[key] = value
if target == "local":
    kv["console"] = "https://127.0.0.1:9443"
    kv["console_http"] = "http://127.0.0.1:9120"
    kv["url"] = "http://127.0.0.1:9119"
else:
    kv["console"] = "https://10.216.4.80"
    kv.pop("console_http", None)
    kv["url"] = "http://10.216.4.80:9119"
order = ("console", "console_http", "url", "session_token", "username", "password")
lines = [f"{key}={kv[key]}" for key in order if key in kv]
for key, value in kv.items():
    if key not in order:
        lines.append(f"{key}={value}")
path.write_text("\n".join(lines) + "\n")


def upsert(env_path: Path, values: dict[str, str]) -> None:
    rows = env_path.read_text().splitlines() if env_path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in rows:
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            out.append(line)
            continue
        key, _current = line.split("=", 1)
        if key in values:
            out.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(out) + "\n")


public = {"HERMES_DASHBOARD_PUBLIC_URL": kv["url"]}
upsert(deploy / ".env", public)
upsert(deploy / "hermes" / "supervisor" / ".env", public)
PY

URL="$(python3 -c "from pathlib import Path; print(next(l.split('=',1)[1] for l in Path('$DEPLOY_DIR/.local-login').read_text().splitlines() if l.startswith('url=')))")"

echo "Hermes Desktop (Remote gateway)"
echo "  Gateway URL: $URL"
echo "  Authentication: Session token"
echo "  Paste session_token from $DEPLOY_DIR/.local-login"
echo "  Extra headers: leave empty. Do not use OAuth."
echo "  Do not use https://…/dash or https://…/bots/… — Electron rejects the Lab CA."
echo "Restart hermes-agent and all six hermes-* specialists after this script"
echo "so dashboard.public_url matches the published IP:port."
echo "Open $URL in a browser to confirm the dashboard before Desktop Test."
if [[ "$TARGET" == "local" ]]; then
  BIND="127.0.0.1"
else
  BIND="10.216.4.80"
fi
echo "Specialist Remote gateways (same session token; not OAuth; IP:port like Athena):"
echo "  http://${BIND}:9121  lab-host"
echo "  http://${BIND}:9122  vector"
echo "  http://${BIND}:9123  cluster-gpu"
echo "  http://${BIND}:9124  llm-edge"
echo "  http://${BIND}:9125  obs"
echo "  http://${BIND}:9126  edge"
echo "Do not use http://${BIND}/bots/… in Desktop — HTTP Test can pass while /api/ws fails."
