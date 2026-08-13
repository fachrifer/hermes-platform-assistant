#!/usr/bin/env bash
# Load pre-built office-assistant images on an air-gapped VM (no registry access).
# Expects linux/amd64 images (override with OFFICE_IMAGE_PLATFORM).
#
# Usage:
#   ./scripts/load-images.sh
#   ./scripts/load-images.sh dist/office-assistant-images-YYYYMMDDTHHMMSSZ.tar
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST="$DEPLOY/dist"
cd "$DEPLOY"

BUNDLE="${1:-}"
PLATFORM="${OFFICE_IMAGE_PLATFORM:-linux/amd64}"
EXPECTED_ARCH="${PLATFORM##*/}"

GATEWAY_IMAGE="${OFFICE_GATEWAY_IMAGE:-hermes-office-gateway:1.0.0}"
HERMES_IMAGE="${HERMES_IMAGE:-nousresearch/hermes-agent:latest}"
PROXY_IMAGE="${DASHBOARD_PROXY_IMAGE:-nginx:1.27-alpine}"

if [[ -z "$BUNDLE" ]]; then
  BUNDLE="$(ls -1t "$DIST"/office-assistant-images-*.tar 2>/dev/null | head -n 1 || true)"
fi
if [[ -z "$BUNDLE" || ! -f "$BUNDLE" ]]; then
  echo "Bundle not found: ${1:-$DIST/office-assistant-images-*.tar}" >&2
  echo "Usage: $0 [office-assistant-images-*.tar]" >&2
  echo "Then:  $SCRIPT_DIR/start.sh" >&2
  exit 1
fi

_arch_of() {
  docker image inspect --format '{{.Architecture}}' "$1" 2>/dev/null || true
}

echo "==> Expected platform: $PLATFORM"
echo "==> Removing previous tags (so stale arm64 does not shadow the new load)…"
docker image rm -f "$GATEWAY_IMAGE" "$HERMES_IMAGE" "$PROXY_IMAGE" 2>/dev/null || true

echo "==> docker load < $BUNDLE"
docker load -i "$BUNDLE"

echo "==> Loaded images:"
docker images --format '{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}' \
  | grep -E 'hermes-office-gateway|hermes-agent|nousresearch/hermes-agent|nginx' || true

fail=0
for img in "$GATEWAY_IMAGE" "$HERMES_IMAGE" "$PROXY_IMAGE"; do
  arch="$(_arch_of "$img")"
  if [[ -z "$arch" ]]; then
    echo "ERROR: image missing after load: $img" >&2
    fail=1
    continue
  fi
  echo "    $img → $arch"
  if [[ "$arch" != "$EXPECTED_ARCH" ]]; then
    echo "ERROR: $img is $arch, need $EXPECTED_ARCH" >&2
    echo "       Re-run export-images.sh on the PC (it builds --platform $PLATFORM), then ship again." >&2
    fail=1
  fi
done

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "==> Platform OK ($PLATFORM). Start with:"
echo "  $SCRIPT_DIR/start.sh"
echo "  # or: docker compose --env-file .env -f docker-compose.yml up -d"
echo "Dashboard: http://10.216.4.80/  (also :9119)"
