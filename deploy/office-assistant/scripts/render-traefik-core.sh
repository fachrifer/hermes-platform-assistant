#!/usr/bin/env bash
# Render Traefik core.yml from template + operator .env token.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE="${OFFICE_TRAEFIK_CORE_TEMPLATE:-$DEPLOY_DIR/edge/traefik-dynamic/core.yml.template}"
OUT="${OFFICE_TRAEFIK_CORE_OUT:-$DEPLOY_DIR/edge/traefik-dynamic/core.yml}"
ENV_FILE="${1:-$DEPLOY_DIR/.env}"

export OFFICE_GATEWAY_TOKEN_SUPERVISOR="${OFFICE_GATEWAY_TOKEN_SUPERVISOR:-}"
if [[ -z "${OFFICE_GATEWAY_TOKEN_SUPERVISOR}" && -f "$ENV_FILE" ]]; then
  OFFICE_GATEWAY_TOKEN_SUPERVISOR="$(
    grep -E '^OFFICE_GATEWAY_TOKEN_SUPERVISOR=' "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"' | tr -d "'"
  )"
  export OFFICE_GATEWAY_TOKEN_SUPERVISOR
fi

python3 - <<'PY' "$TEMPLATE" "$OUT"
import os, sys
from pathlib import Path

template, out = Path(sys.argv[1]), Path(sys.argv[2])
token = os.environ.get("OFFICE_GATEWAY_TOKEN_SUPERVISOR") or "MISSING_SUPERVISOR_TOKEN"
if token == "MISSING_SUPERVISOR_TOKEN":
    print("warning: OFFICE_GATEWAY_TOKEN_SUPERVISOR empty — /api/fleet/ will 401 until set", file=sys.stderr)

BOT_ROLES = (
    ("lab-host", "hermes-lab-host"),
    ("vector", "hermes-vector"),
    ("cluster-gpu", "hermes-cluster-gpu"),
    ("llm-edge", "hermes-llm-edge"),
    ("obs", "hermes-obs"),
    ("edge", "hermes-edge"),
)

def bot_routers(role: str, svc: str, *, http: bool) -> str:
    prefix = f"/bots/{role}"
    tag = f"bots-{role}-http" if http else f"bots-{role}"
    entry = "[web]" if http else "[websecure]"
    tls = "" if http else "      tls: {}\n"
    return f"""    {tag}-ws:
      rule: "PathPrefix(`{prefix}/api/ws`)"
      entryPoints: {entry}
{tls}      priority: 116
      middlewares: [bots-{role}-strip]
      service: {svc}
    {tag}-api:
      rule: "PathPrefix(`{prefix}/api/`)"
      entryPoints: {entry}
{tls}      priority: 96
      middlewares: [bots-{role}-strip]
      service: {svc}
    {tag}-auth:
      rule: "PathPrefix(`{prefix}/auth/`)"
      entryPoints: {entry}
{tls}      priority: 96
      middlewares: [bots-{role}-strip]
      service: {svc}
    {tag}-fonts:
      rule: "PathPrefix(`{prefix}/fonts/`)"
      entryPoints: {entry}
{tls}      priority: 96
      middlewares: [bots-{role}-strip]
      service: {svc}
    {tag}-plugins:
      rule: "PathPrefix(`{prefix}/dashboard-plugins/`)"
      entryPoints: {entry}
{tls}      priority: 96
      middlewares: [bots-{role}-strip]
      service: {svc}
    {tag}-login:
      rule: "PathPrefix(`{prefix}/login`)"
      entryPoints: {entry}
{tls}      priority: 96
      middlewares: [bots-{role}-login-next]
      service: {svc}
    {tag}-dash:
      rule: "PathPrefix(`{prefix}/dash/`)"
      entryPoints: {entry}
{tls}      priority: 86
      middlewares: [bots-{role}-dash-strip]
      service: {svc}
    {tag}:
      rule: "PathPrefix(`{prefix}`)"
      entryPoints: {entry}
{tls}      priority: 85
      middlewares: [bots-{role}-strip]
      service: {svc}
"""


middlewares = []
routers = []
services = []
for role, svc in BOT_ROLES:
    prefix = f"/bots/{role}"
    middlewares.append(
        f"""    bots-{role}-strip:
      stripPrefix:
        prefixes:
          - "{prefix}"
    bots-{role}-dash-strip:
      stripPrefix:
        prefixes:
          - "{prefix}/dash"
    bots-{role}-login-next:
      redirectRegex:
        regex: "^(https?)://([^/]+){prefix}/login(?:\\\\?next=/?|\\\\?next=%2F)?$"
        replacement: "${{1}}://${{2}}{prefix}/dash/login?next={prefix}/dash/"
        permanent: false
"""
    )
    routers.append(bot_routers(role, svc, http=False))
    routers.append(bot_routers(role, svc, http=True))
    services.append(
        f"""    {svc}:
      loadBalancer:
        passHostHeader: true
        servers:
          - url: "http://{svc}:9119"
"""
    )

text = template.read_text(encoding="utf-8")
text = text.replace("__OFFICE_GATEWAY_TOKEN_SUPERVISOR__", token)
text = text.replace("__BOT_MIDDLEWARES__", "\n".join(middlewares).rstrip())
text = text.replace("__BOT_ROUTERS__", "\n".join(routers).rstrip())
text = text.replace("__BOT_SERVICES__", "\n".join(services).rstrip())
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(text, encoding="utf-8")
print(f"wrote {out}")
PY
