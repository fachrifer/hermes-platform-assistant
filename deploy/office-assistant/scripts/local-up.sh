#!/usr/bin/env bash
set -euo pipefail

# Start the office fleet on this laptop for a functionality check.
# Athena console: https://127.0.0.1:9443  (HTTP :9120 redirects)
# Raw Dashboard:  http://127.0.0.1:9119
#
# Fills empty tokens in gitignored .env files. Does not print secrets.
# LLM: OFFICE_LLM_BASE_URL from models.env or the process environment;
# OFFICE_LLM_API_KEY from each hermes/<role>/.env, else the process environment.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

cd "$DEPLOY_DIR"
"$SCRIPT_DIR/copy-env.sh"

python3 - "$DEPLOY_DIR" <<'PY'
import os
import secrets
import sys
from pathlib import Path

deploy = Path(sys.argv[1])
roles = ("supervisor", "lab-host", "ingress", "llm", "cluster-gpu", "vector", "obs")
gateway_keys = (
    "OFFICE_GATEWAY_TOKEN_SUPERVISOR",
    "OFFICE_GATEWAY_TOKEN_LAB_HOST",
    "OFFICE_GATEWAY_TOKEN_INGRESS",
    "OFFICE_GATEWAY_TOKEN_LLM",
    "OFFICE_GATEWAY_TOKEN_CLUSTER",
    "OFFICE_GATEWAY_TOKEN_VECTOR",
    "OFFICE_GATEWAY_TOKEN_OBS",
    "OFFICE_GATEWAY_TOKEN_APPROVER",
)


def load(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def upsert(path: Path, values: dict[str, str], only_empty: bool = True) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    seen = set()
    out = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            out.append(line)
            continue
        key, current = line.split("=", 1)
        seen.add(key)
        if key in values and (not only_empty or not current.strip()):
            out.append(f"{key}={values[key]}")
        else:
            out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n")


root_env = deploy / ".env"
supervisor = deploy / "hermes" / "supervisor" / ".env"
root = load(root_env)
sup = load(supervisor)

kube = deploy / ".kubeconfig.local"
if not kube.exists():
    kube.write_text(
        "apiVersion: v1\nkind: Config\nclusters: []\nusers: []\ncontexts: []\ncurrent-context: \"\"\n"
    )

upsert(root_env, {key: secrets.token_urlsafe(32) for key in gateway_keys})
upsert(
    root_env,
    {"OFFICE_SERVICE_URLS": "gateway=http://office-gateway:8080/health"},
)
dash_user = sup.get("HERMES_DASHBOARD_USERNAME") or root.get("HERMES_DASHBOARD_USERNAME") or "athena"
dash_pass = sup.get("HERMES_DASHBOARD_PASSWORD") or root.get("HERMES_DASHBOARD_PASSWORD") or secrets.token_urlsafe(12)
dash_secret = (
    root.get("HERMES_DASHBOARD_BASIC_AUTH_SECRET")
    or sup.get("HERMES_DASHBOARD_BASIC_AUTH_SECRET")
    or secrets.token_urlsafe(32)
)
local = {
    "OFFICE_KUBECONFIG": str(kube),
    "OFFICE_GATEWAY_CONTEXT": "../..",
    "HERMES_DASHBOARD_PUBLISH": "127.0.0.1:9119",
    "HERMES_CONSOLE_PUBLISH": "127.0.0.1:9120",
    "HERMES_CONSOLE_TLS_PUBLISH": "127.0.0.1:9444",
    "HERMES_CONSOLE_HERMES_PUBLISH": "127.0.0.1:9443",
    "HERMES_CONSOLE_TLS_HOST": "127.0.0.1:9443",
    "HERMES_DASHBOARD_URL": "http://hermes-agent:9119",
    "HERMES_DASHBOARD_USERNAME": dash_user,
    "HERMES_DASHBOARD_PASSWORD": dash_pass,
    "HERMES_DASHBOARD_BASIC_AUTH_SECRET": dash_secret,
    "HERMES_BOT_PUBLIC_ORIGIN": "http://127.0.0.1:9120",
    "HERMES_BOT_BIND": "127.0.0.1",
}
upsert(root_env, local, only_empty=False)
upsert(
    supervisor,
    {
        "HERMES_DASHBOARD_USERNAME": dash_user,
        "HERMES_DASHBOARD_PASSWORD": dash_pass,
        "HERMES_DASHBOARD_BASIC_AUTH_SECRET": dash_secret,
    },
    only_empty=False,
)

base_url = os.environ.get("OFFICE_LLM_BASE_URL", "").strip()
if base_url:
    upsert(deploy / "models.env", {"OFFICE_LLM_BASE_URL": base_url}, only_empty=False)
base_url = load(deploy / "models.env").get("OFFICE_LLM_BASE_URL", "")
llm_key = os.environ.get("OFFICE_LLM_API_KEY", "").strip()
missing = []
for role in roles:
    path = deploy / "hermes" / role / ".env"
    if llm_key:
        upsert(path, {"OFFICE_LLM_API_KEY": llm_key})
    if not load(path).get("OFFICE_LLM_API_KEY"):
        missing.append(role)

if not base_url:
    print("OFFICE_LLM_BASE_URL is empty: set it in models.env (see models.env.example)")
if missing:
    print("OFFICE_LLM_API_KEY is empty for: " + ", ".join(missing))
PY

"$SCRIPT_DIR/pair-env.sh"
"$SCRIPT_DIR/ensure-approver.sh"

python3 - "$DEPLOY_DIR" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1]) / ".local-login"
rows = dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)
rows.update(
    console="https://127.0.0.1:9443",
    console_http="http://127.0.0.1:9120",
    url="http://127.0.0.1:9119",
)
path.write_text("".join(f"{k}={v}\n" for k, v in rows.items()))
print("wrote dashboard and approver logins to deploy/office-assistant/.local-login")
PY

if [[ -S /var/run/docker.sock ]]; then
  if stat -f %g /var/run/docker.sock >/dev/null 2>&1; then
    export DOCKER_GID
    DOCKER_GID="$(stat -f %g /var/run/docker.sock)"
  else
    export DOCKER_GID
    DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
  fi
fi

TLS_IP=127.0.0.1 "$SCRIPT_DIR/init-lab-ca.sh"
TLS_IP=127.0.0.1 "$SCRIPT_DIR/issue-edge-cert.sh"
python3 "$SCRIPT_DIR/render-edge-traefik.py"
"$SCRIPT_DIR/render-traefik-core.sh"

echo "docker compose up (Athena console https://127.0.0.1:9443)"
docker compose -f "$DEPLOY_DIR/docker-compose.yml" -f "$DEPLOY_DIR/docker-compose.local.yml" up -d --build --force-recreate --remove-orphans
docker compose -f "$DEPLOY_DIR/docker-compose.yml" -f "$DEPLOY_DIR/docker-compose.local.yml" ps
echo "Open https://127.0.0.1:9443  (import ca/ca.crt into the trust store once; HTTP :9120 redirects)"
echo "Approvals: https://127.0.0.1:9443/approvals/  (approver_user / approver_password in .local-login)"
echo "Raw Hermes chat: http://127.0.0.1:9119  (credentials in deploy/office-assistant/.local-login)"
