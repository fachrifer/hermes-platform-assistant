#!/usr/bin/env bash
set -euo pipefail

# Start the office fleet on this laptop for a functionality check.
# Athena console: http://127.0.0.1:9120  (specialists + VTuber + chat)
# Raw Dashboard:  http://127.0.0.1:9119
# Gateway health: http://127.0.0.1:18080/health
#
# Fills empty tokens in gitignored .env files. Does not print secrets.
# Chat uses OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL from models.env,
# the operator .env, hermes/supervisor/.env, or the process environment.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

cd "$DEPLOY_DIR"
"$SCRIPT_DIR/copy-env.sh"

python3 - "$DEPLOY_DIR" <<'PY'
import os
import secrets
import sys
from pathlib import Path

deploy = Path(sys.argv[1])
roles = ("supervisor", "lab-host", "vector", "cluster-gpu", "llm-edge", "obs", "edge")


def load(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


def upsert(path: Path, values: dict[str, str], only_empty: bool = True) -> None:
    lines = load(path)
    keys_seen = set()
    out = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            out.append(line)
            continue
        key, current = line.split("=", 1)
        keys_seen.add(key)
        if key in values and (not only_empty or not current.strip()):
            out.append(f"{key}={values[key]}")
        else:
            out.append(line)
    for key, value in values.items():
        if key not in keys_seen:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n")


def get(path: Path, key: str) -> str:
    for line in load(path):
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return ""


root_env = deploy / ".env"
supervisor = deploy / "hermes" / "supervisor" / ".env"
login_path = deploy / ".local-login"

gw_tokens = {
    "OFFICE_GATEWAY_TOKEN_SUPERVISOR": get(root_env, "OFFICE_GATEWAY_TOKEN_SUPERVISOR") or secrets.token_urlsafe(24),
    "OFFICE_GATEWAY_TOKEN_LAB_HOST": get(root_env, "OFFICE_GATEWAY_TOKEN_LAB_HOST") or secrets.token_urlsafe(24),
    "OFFICE_GATEWAY_TOKEN_VECTOR": get(root_env, "OFFICE_GATEWAY_TOKEN_VECTOR") or secrets.token_urlsafe(24),
    "OFFICE_GATEWAY_TOKEN_CLUSTER": get(root_env, "OFFICE_GATEWAY_TOKEN_CLUSTER") or secrets.token_urlsafe(24),
    "OFFICE_GATEWAY_TOKEN_LLM": get(root_env, "OFFICE_GATEWAY_TOKEN_LLM") or secrets.token_urlsafe(24),
    "OFFICE_GATEWAY_TOKEN_OBS": get(root_env, "OFFICE_GATEWAY_TOKEN_OBS") or secrets.token_urlsafe(24),
    "OFFICE_GATEWAY_TOKEN_EDGE": get(root_env, "OFFICE_GATEWAY_TOKEN_EDGE") or secrets.token_urlsafe(24),
}
a2a = {
    "lab-host": get(supervisor, "A2A_TOKEN_LAB_HOST") or secrets.token_urlsafe(24),
    "vector": get(supervisor, "A2A_TOKEN_VECTOR") or secrets.token_urlsafe(24),
    "cluster-gpu": get(supervisor, "A2A_TOKEN_CLUSTER_GPU") or secrets.token_urlsafe(24),
    "llm-edge": get(supervisor, "A2A_TOKEN_LLM_EDGE") or secrets.token_urlsafe(24),
    "obs": get(supervisor, "A2A_TOKEN_OBS") or secrets.token_urlsafe(24),
    "edge": get(supervisor, "A2A_TOKEN_EDGE") or secrets.token_urlsafe(24),
}
dash_user = get(supervisor, "HERMES_DASHBOARD_USERNAME") or "athena"
dash_pass = get(supervisor, "HERMES_DASHBOARD_PASSWORD") or secrets.token_urlsafe(12)
dash_secret = (
    get(root_env, "HERMES_DASHBOARD_BASIC_AUTH_SECRET")
    or get(supervisor, "HERMES_DASHBOARD_BASIC_AUTH_SECRET")
    or secrets.token_urlsafe(32)
)


def load_map(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in load(path):
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value:
            out[key] = value
    return out


model_keys: dict[str, str] = {}
# Last source wins: role .env defaults, then operator .env, then models.env, then process env.
for src in (supervisor, root_env, deploy / "models.env"):
    data = load_map(src)
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
        if data.get(key):
            model_keys[key] = data[key]
for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
    env_value = os.environ.get(key, "").strip()
    if env_value:
        model_keys[key] = env_value
openai_key = model_keys.get("OPENAI_API_KEY", "")


kube = deploy / ".kubeconfig.local"
if not kube.exists():
    kube.write_text(
        "apiVersion: v1\nkind: Config\nclusters: []\nusers: []\ncontexts: []\ncurrent-context: \"\"\n"
    )

existing_urls = get(root_env, "OFFICE_SERVICE_URLS")
existing_kube = get(root_env, "OFFICE_KUBECONFIG")
if existing_kube in {"", "/path/to/kubeconfig"}:
    existing_kube = str(kube)

upsert(
    root_env,
    {
        **gw_tokens,
        "HERMES_DASHBOARD_PUBLISH": "127.0.0.1:9119",
        "OFFICE_KUBECONFIG": existing_kube if existing_kube != "/path/to/kubeconfig" else str(kube),
        "OFFICE_SERVICE_URLS": existing_urls or "gateway=http://office-gateway:8080/health",
        "OFFICE_GATEWAY_CONTEXT": "../..",
        "HERMES_CONSOLE_PUBLISH": "127.0.0.1:9120",
        "HERMES_CONSOLE_TLS_PUBLISH": "127.0.0.1:9443",
    },
    only_empty=True,
)
# Local laptop must not mount the example placeholder path.
upsert(
    root_env,
    {
        "OFFICE_KUBECONFIG": str(kube),
        "HERMES_DASHBOARD_PUBLISH": "127.0.0.1:9119",
        "HERMES_CONSOLE_PUBLISH": "127.0.0.1:9120",
        "HERMES_CONSOLE_TLS_PUBLISH": "127.0.0.1:9443",
        "HERMES_DASHBOARD_URL": "http://hermes-agent:9119",
        "HERMES_DASHBOARD_USERNAME": dash_user,
        "HERMES_DASHBOARD_PASSWORD": dash_pass,
        "HERMES_DASHBOARD_BASIC_AUTH_SECRET": dash_secret,
        "HERMES_BOT_PUBLIC_ORIGIN": "http://127.0.0.1:9120",
        "HERMES_BOT_BIND": "127.0.0.1",
        "OFFICE_GATEWAY_CONTEXT": "../..",
    },
    only_empty=False,
)
upsert(
    supervisor,
    {
        "OFFICE_GATEWAY_TOKEN": gw_tokens["OFFICE_GATEWAY_TOKEN_SUPERVISOR"],
        "A2A_TOKEN_LAB_HOST": a2a["lab-host"],
        "A2A_TOKEN_VECTOR": a2a["vector"],
        "A2A_TOKEN_CLUSTER_GPU": a2a["cluster-gpu"],
        "A2A_TOKEN_LLM_EDGE": a2a["llm-edge"],
        "A2A_TOKEN_OBS": a2a["obs"],
        "A2A_TOKEN_EDGE": a2a["edge"],
        "HERMES_DASHBOARD_USERNAME": dash_user,
        "HERMES_DASHBOARD_PASSWORD": dash_pass,
        "HERMES_DASHBOARD_BASIC_AUTH_SECRET": dash_secret,
    },
)
if model_keys:
    upsert(supervisor, model_keys, only_empty=False)
role_token = {
    "lab-host": gw_tokens["OFFICE_GATEWAY_TOKEN_LAB_HOST"],
    "vector": gw_tokens["OFFICE_GATEWAY_TOKEN_VECTOR"],
    "cluster-gpu": gw_tokens["OFFICE_GATEWAY_TOKEN_CLUSTER"],
    "llm-edge": gw_tokens["OFFICE_GATEWAY_TOKEN_LLM"],
    "obs": gw_tokens["OFFICE_GATEWAY_TOKEN_OBS"],
    "edge": gw_tokens["OFFICE_GATEWAY_TOKEN_EDGE"],
}
for role in ("lab-host", "vector", "cluster-gpu", "llm-edge", "obs", "edge"):
    payload = {
        "OFFICE_GATEWAY_TOKEN": role_token[role],
        "A2A_BEARER_TOKEN": a2a[role],
        **model_keys,
    }
    upsert(deploy / "hermes" / role / ".env", payload, only_empty=False if model_keys else True)

peer_map = {
    "lab-host": "HERMES_PEER_LAB_HOST_KEY",
    "vector": "HERMES_PEER_VECTOR_KEY",
    "cluster-gpu": "HERMES_PEER_CLUSTER_GPU_KEY",
    "llm-edge": "HERMES_PEER_LLM_EDGE_KEY",
    "obs": "HERMES_PEER_OBS_KEY",
    "edge": "HERMES_PEER_EDGE_KEY",
}
peer_keys = {}
for role, peer_env in peer_map.items():
    role_path = deploy / "hermes" / role / ".env"
    api_key = get(role_path, "API_SERVER_KEY") or secrets.token_urlsafe(32)
    upsert(role_path, {"API_SERVER_KEY": api_key}, only_empty=True)
    peer_keys[peer_env] = get(role_path, "API_SERVER_KEY") or api_key
upsert(supervisor, peer_keys)

session = (
    get(root_env, "HERMES_DASHBOARD_SESSION_TOKEN")
    or get(supervisor, "HERMES_DASHBOARD_SESSION_TOKEN")
    or secrets.token_urlsafe(32)
)
upsert(root_env, {"HERMES_DASHBOARD_SESSION_TOKEN": session}, only_empty=False)
upsert(supervisor, {"HERMES_DASHBOARD_SESSION_TOKEN": session}, only_empty=False)
upsert(
    root_env,
    {"HERMES_DASHBOARD_PUBLIC_URL": "http://127.0.0.1:9119"},
    only_empty=False,
)
upsert(
    supervisor,
    {"HERMES_DASHBOARD_PUBLIC_URL": "http://127.0.0.1:9119"},
    only_empty=False,
)
for role in ("lab-host", "vector", "cluster-gpu", "llm-edge", "obs", "edge"):
    upsert(
        deploy / "hermes" / role / ".env",
        {"HERMES_DASHBOARD_SESSION_TOKEN": session},
        only_empty=False,
    )

login_path.write_text(
    f"console=https://127.0.0.1:9443\n"
    f"console_http=http://127.0.0.1:9120\n"
    f"url=http://127.0.0.1:9119\n"
    f"session_token={session}\n"
    f"username={dash_user}\npassword={dash_pass}\n"
)
print("wrote dashboard login to deploy/office-assistant/.local-login")
model_name = model_keys.get("OPENAI_MODEL", "")
base_url = model_keys.get("OPENAI_BASE_URL", "")
if openai_key and model_name:
    host = base_url.split("/")[2] if "://" in base_url else base_url
    print(f"using OPENAI_MODEL={model_name} via {host}")
elif not openai_key:
    print("OPENAI_API_KEY is empty: put a reachable key in models.env (see models.env.example)")
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
echo "Raw Hermes chat: http://127.0.0.1:9119  (credentials in deploy/office-assistant/.local-login)"
