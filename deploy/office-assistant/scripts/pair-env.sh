#!/usr/bin/env bash
set -euo pipefail

# Pair office-gateway tokens, peer API keys and the dashboard session token
# across the role .env files so they match the operator .env.
# Does not print secret values.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

python3 - "$DEPLOY_DIR" <<'PY'
from pathlib import Path
import os
import secrets
import sys

deploy = Path(sys.argv[1])
gateway_map = {
    "supervisor": "OFFICE_GATEWAY_TOKEN_SUPERVISOR",
    "lab-host": "OFFICE_GATEWAY_TOKEN_LAB_HOST",
    "ingress": "OFFICE_GATEWAY_TOKEN_INGRESS",
    "llm": "OFFICE_GATEWAY_TOKEN_LLM",
    "cluster-gpu": "OFFICE_GATEWAY_TOKEN_CLUSTER",
    "vector": "OFFICE_GATEWAY_TOKEN_VECTOR",
    "obs": "OFFICE_GATEWAY_TOKEN_OBS",
}
peer_map = {
    r: f"HERMES_PEER_PEER_{r.upper().replace('-', '_')}_KEY"
    for r in ("lab-host", "ingress", "llm", "cluster-gpu", "vector", "obs")
}
LOGIN_KEEP = ("approver_user", "approver_password")


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
supervisor_env = deploy / "hermes" / "supervisor" / ".env"
supervisor = load(supervisor_env)
missing = [key for key in gateway_map.values() if not root.get(key)]
if missing:
    raise SystemExit("pair-env: missing " + ", ".join(missing) + " in .env")

for role, env_key in gateway_map.items():
    upsert(deploy / "hermes" / role / ".env", {"OFFICE_GATEWAY_TOKEN": root[env_key]})

peer_keys = {}
for role, peer_env in peer_map.items():
    role_env = load(deploy / "hermes" / role / ".env")
    api_key = (role_env.get("API_SERVER_KEY") or "").strip() or secrets.token_urlsafe(32)
    upsert(deploy / "hermes" / role / ".env", {"API_SERVER_KEY": api_key})
    peer_keys[peer_env] = api_key
upsert(supervisor_env, peer_keys)
if not (supervisor.get("API_SERVER_KEY") or "").strip():
    upsert(supervisor_env, {"API_SERVER_KEY": secrets.token_urlsafe(32)})

session_key = "HERMES_DASHBOARD_SESSION_TOKEN"
session = (
    (root.get(session_key) or "").strip()
    or (supervisor.get(session_key) or "").strip()
    or secrets.token_urlsafe(32)
)
upsert(deploy / ".env", {session_key: session})
for role in gateway_map:
    upsert(deploy / "hermes" / role / ".env", {session_key: session})

publish = (root.get("HERMES_DASHBOARD_PUBLISH") or "10.216.4.80:9119").strip()
public_url = f"http://{publish}"
upsert(deploy / ".env", {"HERMES_DASHBOARD_PUBLIC_URL": public_url})
upsert(supervisor_env, {"HERMES_DASHBOARD_PUBLIC_URL": public_url})
supervisor = load(supervisor_env)
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
login_path = deploy / ".local-login"
kept = {k: v for k, v in load(login_path).items() if k in LOGIN_KEEP}
lines = [
    f"console={console}",
    f"url=http://{publish}",
    f"session_token={session}",
    f"username={user}",
    f"password={password}",
] + [f"{k}={v}" for k, v in kept.items()]
login_path.write_text("\n".join(lines) + "\n")
os.chmod(login_path, 0o600)
print("paired gateway tokens, peer keys and session token across hermes role .env files")
PY
