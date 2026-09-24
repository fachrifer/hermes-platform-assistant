#!/usr/bin/env bash
set -euo pipefail

# Pair office-gateway and A2A tokens so specialists match the operator .env.
# Does not print secret values.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

python3 - "$DEPLOY_DIR" <<'PY'
from pathlib import Path
import secrets
import sys

deploy = Path(sys.argv[1])
gateway_map = {
    "supervisor": "OFFICE_GATEWAY_TOKEN_SUPERVISOR",
    "lab-host": "OFFICE_GATEWAY_TOKEN_LAB_HOST",
    "vector": "OFFICE_GATEWAY_TOKEN_VECTOR",
    "cluster-gpu": "OFFICE_GATEWAY_TOKEN_CLUSTER",
    "llm-edge": "OFFICE_GATEWAY_TOKEN_LLM",
    "obs": "OFFICE_GATEWAY_TOKEN_OBS",
    "edge": "OFFICE_GATEWAY_TOKEN_EDGE",
}
a2a_map = {
    "lab-host": "A2A_TOKEN_LAB_HOST",
    "vector": "A2A_TOKEN_VECTOR",
    "cluster-gpu": "A2A_TOKEN_CLUSTER_GPU",
    "llm-edge": "A2A_TOKEN_LLM_EDGE",
    "obs": "A2A_TOKEN_OBS",
    "edge": "A2A_TOKEN_EDGE",
}
peer_map = {
    "lab-host": "HERMES_PEER_LAB_HOST_KEY",
    "vector": "HERMES_PEER_VECTOR_KEY",
    "cluster-gpu": "HERMES_PEER_CLUSTER_GPU_KEY",
    "llm-edge": "HERMES_PEER_LLM_EDGE_KEY",
    "obs": "HERMES_PEER_OBS_KEY",
    "edge": "HERMES_PEER_EDGE_KEY",
}


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


def upsert(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    seen: set[str] = set()
    out = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            out.append(line)
            continue
        key, current = line.split("=", 1)
        if key in values:
            out.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")


root = load(deploy / ".env")
supervisor = load(deploy / "hermes" / "supervisor" / ".env")
missing = [key for key in gateway_map.values() if not root.get(key)]
if missing:
    raise SystemExit("pair-env: missing " + ", ".join(missing) + " in .env")
missing_a2a = [key for key in a2a_map.values() if not supervisor.get(key)]
if missing_a2a:
    raise SystemExit(
        "pair-env: missing " + ", ".join(missing_a2a) + " in hermes/supervisor/.env"
    )

upsert(
    deploy / "hermes" / "supervisor" / ".env",
    {"OFFICE_GATEWAY_TOKEN": root[gateway_map["supervisor"]]},
)
for role, env_key in gateway_map.items():
    if role == "supervisor":
        continue
    upsert(
        deploy / "hermes" / role / ".env",
        {
            "OFFICE_GATEWAY_TOKEN": root[env_key],
            "A2A_BEARER_TOKEN": supervisor[a2a_map[role]],
        },
    )
peer_keys = {}
for role, peer_env in peer_map.items():
    role_env = load(deploy / "hermes" / role / ".env")
    api_key = (role_env.get("API_SERVER_KEY") or "").strip() or secrets.token_urlsafe(32)
    upsert(deploy / "hermes" / role / ".env", {"API_SERVER_KEY": api_key})
    peer_keys[peer_env] = api_key
upsert(deploy / "hermes" / "supervisor" / ".env", peer_keys)
session_key = "HERMES_DASHBOARD_SESSION_TOKEN"
session = (
    (root.get(session_key) or "").strip()
    or (supervisor.get(session_key) or "").strip()
    or secrets.token_urlsafe(32)
)
upsert(deploy / ".env", {session_key: session})
upsert(deploy / "hermes" / "supervisor" / ".env", {session_key: session})
for role in peer_map:
    upsert(deploy / "hermes" / role / ".env", {session_key: session})
publish = (root.get("HERMES_DASHBOARD_PUBLISH") or "10.216.4.80:9119").strip()
public_url = f"http://{publish}"
upsert(deploy / ".env", {"HERMES_DASHBOARD_PUBLIC_URL": public_url})
upsert(
    deploy / "hermes" / "supervisor" / ".env",
    {"HERMES_DASHBOARD_PUBLIC_URL": public_url},
)
user = (
    (supervisor.get("HERMES_DASHBOARD_USERNAME") or "").strip()
    or (root.get("HERMES_DASHBOARD_USERNAME") or "").strip()
)
password = (
    (supervisor.get("HERMES_DASHBOARD_PASSWORD") or "").strip()
    or (root.get("HERMES_DASHBOARD_PASSWORD") or "").strip()
)
tls = (root.get("HERMES_CONSOLE_TLS_PUBLISH") or "10.216.4.80:443").strip()
host, _, port = tls.partition(":")
console = f"https://{host}" if port in {"", "443"} else f"https://{host}:{port}"
(deploy / ".local-login").write_text(
    f"console={console}\n"
    f"url=http://{publish}\n"
    f"session_token={session}\n"
    f"username={user}\n"
    f"password={password}\n"
)
print("paired office-gateway and A2A tokens across hermes role .env files")
PY
