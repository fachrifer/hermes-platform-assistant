#!/usr/bin/env bash
# Start the office-assistant stack from already-loaded local images.
# Run on the VM: /home/timai/hermes-assistant/scripts/start.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$DEPLOY"

COMPOSE=(docker compose --env-file .env -f docker-compose.yml)

if [[ ! -f docker-compose.yml ]]; then
  echo "docker-compose.yml not found in $DEPLOY" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "==> Created .env from .env.example — edit keys/passwords, then re-run."
fi
if [[ ! -f hermes/.env ]]; then
  mkdir -p hermes
  cp hermes/.env.example hermes/.env
  echo "==> Created hermes/.env from example — edit keys, then re-run."
fi

echo "==> Starting office-gateway + hermes-agent + dashboard-proxy (no registry pull)"
"${COMPOSE[@]}" up -d --force-recreate
"${COMPOSE[@]}" ps

echo "==> Waiting for containers…"
sleep 3

echo "==> office-gateway /health"
if ! "${COMPOSE[@]}" exec -T office-gateway \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5).read().decode())"; then
  echo "gateway health failed — logs:" >&2
  "${COMPOSE[@]}" logs --tail 80 office-gateway >&2
  exit 1
fi

echo "==> hermes-agent"
"${COMPOSE[@]}" logs --tail 20 hermes-agent || true

HOST="${HERMES_DASHBOARD_HOST_HINT:-10.216.4.80}"
echo
echo "Dashboard: http://${HOST}/  (also :9119)"
echo "Login: HERMES_DASHBOARD_BASIC_AUTH_USERNAME / PASSWORD in .env"
echo "Stop:  ${COMPOSE[*]} down"
