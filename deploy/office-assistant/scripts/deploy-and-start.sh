#!/usr/bin/env bash
set -euo pipefail

# Lab VM entrypoint. Never overwrites existing .env / models.env / hermes/*/.env.
# docker load the air-gap tarball if present, then compose down + up.
#
# Usage:
#   ./scripts/deploy-and-start.sh
#   ./scripts/deploy-and-start.sh /path/to/office-fleet-images.tar.gz

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

if ! office_is_lab_tree; then
  echo "error: deploy-and-start.sh runs on the Lab VM (/home/timai/hermes-assistant)." >&2
  echo "This is the laptop checkout. The image tarball is linux/amd64 for that host." >&2
  echo "On this Mac:" >&2
  echo "  ./scripts/local-up.sh   # start locally at http://127.0.0.1:9120" >&2
  echo "  ./scripts/deploy.sh     # copy tarball + start on timai@10.216.4.80" >&2
  exit 1
fi

TARBALL="${1:-$DEFAULT_TARBALL}"
: "${OFFICE_GW_IMAGE:=office-gw:local}"
: "${BUSYBOX_IMAGE:=busybox:1.36}"
: "${CONSOLE_IMAGE:=nginx:1.27-alpine}"
: "${TRAEFIK_IMAGE:=traefik:v3.3}"

"$SCRIPT_DIR/copy-env.sh"

if grep -qE '^OFFICE_KUBECONFIG=(/path/to/kubeconfig)?$' "$DEPLOY_DIR/.env" 2>/dev/null \
    || ! grep -qE '^OFFICE_KUBECONFIG=' "$DEPLOY_DIR/.env" 2>/dev/null; then
  echo "warning: OFFICE_KUBECONFIG is unset or placeholder; cluster-gpu k8s reads will skip kubeconfig copy" >&2
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

HOST_ARCH="$(office_docker_arch)"

require_image_arch() {
  local image="$1"
  local img_arch
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    return 0
  fi
  img_arch="$(office_image_arch "$image")"
  if [[ -z "$img_arch" || -z "$HOST_ARCH" || "$img_arch" == "$HOST_ARCH" ]]; then
    return 0
  fi
  echo "architecture mismatch: $image is $img_arch, host is $HOST_ARCH" >&2
  echo "error: this host cannot pull Docker Hub; ship linux/${HOST_ARCH} images in the tarball." >&2
  echo "On a networked machine: OFFICE_IMAGE_PLATFORM=linux/${HOST_ARCH} ./scripts/save-images.sh" >&2
  echo "Then copy images/office-fleet-images.tar.gz here and re-run ./scripts/deploy-and-start.sh" >&2
  return 1
}

require_image_present() {
  local image="$1"
  if docker image inspect "$image" >/dev/null 2>&1; then
    return 0
  fi
  echo "error: missing image $image (Lab cannot pull Docker Hub)." >&2
  echo "On a networked machine: ./scripts/save-images.sh" >&2
  echo "Then copy images/office-fleet-images.tar.gz here and re-run ./scripts/deploy-and-start.sh" >&2
  return 1
}

require_image_arch "$BUSYBOX_IMAGE"
require_image_arch "$CONSOLE_IMAGE"
require_image_arch "$TRAEFIK_IMAGE"
require_image_arch "$(office_hermes_image)"
require_image_arch "$OFFICE_GW_IMAGE"
require_image_present "$TRAEFIK_IMAGE"
require_image_present "$CONSOLE_IMAGE"

cd "$DEPLOY_DIR"
export OFFICE_GATEWAY_CONTEXT="${OFFICE_GATEWAY_CONTEXT:-.}"
DASHBOARD="$(office_env_value HERMES_DASHBOARD_PUBLISH 10.216.4.80:9119)"
CONSOLE="$(office_env_value HERMES_CONSOLE_PUBLISH 10.216.4.80:80)"
TLS="$(office_env_value HERMES_CONSOLE_TLS_PUBLISH 10.216.4.80:443)"

TLS_IP="${TLS_IP:-10.216.4.80}" "$SCRIPT_DIR/init-lab-ca.sh"
TLS_IP="${TLS_IP:-10.216.4.80}" "$SCRIPT_DIR/issue-edge-cert.sh"
python3 "$SCRIPT_DIR/render-edge-traefik.py"
"$SCRIPT_DIR/render-traefik-core.sh"
"$SCRIPT_DIR/ensure-approver.sh"


echo "docker compose down --remove-orphans"
docker compose down --remove-orphans
office_stop_port_holders "$DASHBOARD"
office_stop_port_holders "$CONSOLE"
office_stop_port_holders "$TLS"

if docker image inspect "$OFFICE_GW_IMAGE" >/dev/null 2>&1; then
  echo "docker compose up -d (using existing $OFFICE_GW_IMAGE)"
  docker compose up -d
else
  echo "docker compose up -d --build"
  docker compose up -d --build
fi

echo "Athena console (HTTPS): https://${TLS}"
echo "HTTP redirect: http://${CONSOLE}"
echo "Dashboard (raw): http://${DASHBOARD}"
echo "Status: $SCRIPT_DIR/status.sh"
echo "Firewall (optional): LAB_FIREWALL_APPLY=1 $SCRIPT_DIR/lab-firewall-https-edge.sh"
