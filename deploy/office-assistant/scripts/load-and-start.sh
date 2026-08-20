#!/usr/bin/env bash
set -euo pipefail

# Lab VM entrypoint: copy missing env templates, optionally docker load an
# air-gap tarball, then docker compose up.
#
# Usage:
#   ./scripts/load-and-start.sh
#   ./scripts/load-and-start.sh /path/to/office-fleet-images.tar.gz

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

TARBALL="${1:-$DEFAULT_TARBALL}"
: "${OFFICE_GW_IMAGE:=office-gw:local}"

"$SCRIPT_DIR/copy-env.sh"

if grep -qE '^OFFICE_KUBECONFIG=/path/to/kubeconfig$' "$DEPLOY_DIR/.env" 2>/dev/null; then
  echo "warning: OFFICE_KUBECONFIG is still the placeholder; cluster-gpu k8s reads will skip kubeconfig copy" >&2
fi

if [[ -f "$TARBALL" ]]; then
  echo "docker load $TARBALL"
  gzip -dc "$TARBALL" | docker load
elif [[ -f "${TARBALL%.gz}" ]]; then
  echo "docker load ${TARBALL%.gz}"
  docker load < "${TARBALL%.gz}"
else
  echo "no tarball at $TARBALL; using local images or compose --build"
fi

cd "$DEPLOY_DIR"
if docker image inspect "$OFFICE_GW_IMAGE" >/dev/null 2>&1; then
  echo "docker compose up -d (using existing $OFFICE_GW_IMAGE)"
  docker compose up -d
else
  echo "docker compose up -d --build"
  docker compose up -d --build
fi

DASHBOARD="$(grep -E '^HERMES_DASHBOARD_PUBLISH=' "$DEPLOY_DIR/.env" | tail -n 1 | cut -d= -f2- || true)"
DASHBOARD="${DASHBOARD:-10.216.4.80:9119}"
echo "Dashboard: http://${DASHBOARD}"
echo "Status: $SCRIPT_DIR/status.sh"
