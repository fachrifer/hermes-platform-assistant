#!/usr/bin/env bash
# Copy office-assistant configs + image tar to the air-gapped VM.
# Default target: timai@10.216.4.80:/home/timai/hermes-assistant
#
# Usage:
#   ./ship-to-vm.sh                         # latest dist/office-assistant-images-*.tar
#   ./ship-to-vm.sh path/to/bundle.tar
#   ./ship-to-vm.sh --scripts-only           # compose, hermes config, scripts; no image tar
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST="${OFFICE_ASSISTANT_BUNDLE_DIR:-$DEPLOY/dist}"

VM_HOST="${OFFICE_VM_HOST:-10.216.4.80}"
VM_USER="${OFFICE_VM_USER:-timai}"
VM_DIR="${OFFICE_VM_DIR:-/home/timai/hermes-assistant}"
REMOTE="${VM_USER}@${VM_HOST}:${VM_DIR}"

SCRIPTS_ONLY=0
BUNDLE="${1:-}"
if [[ "${BUNDLE}" == "--scripts-only" ]]; then
  SCRIPTS_ONLY=1
  BUNDLE=""
fi

if [[ "$SCRIPTS_ONLY" -eq 0 ]]; then
  if [[ -z "$BUNDLE" ]]; then
    BUNDLE="$(ls -1t "$DIST"/office-assistant-images-*.tar 2>/dev/null | head -n 1 || true)"
  fi
  if [[ -z "$BUNDLE" || ! -f "$BUNDLE" ]]; then
    echo "No image bundle found. Pass a .tar, run export-images.sh, or use --scripts-only." >&2
    echo "Usage: $0 [office-assistant-images-*.tar | --scripts-only]" >&2
    exit 1
  fi
fi

echo "==> Target $REMOTE"
if [[ "$SCRIPTS_ONLY" -eq 1 ]]; then
  echo "==> Mode    scripts + configs (no image tar)"
else
  echo "==> Bundle  $BUNDLE"
fi

ssh "${VM_USER}@${VM_HOST}" "mkdir -p '$VM_DIR/dist' '$VM_DIR/scripts' '$VM_DIR/hermes'"

rsync -az --delete \
  --exclude '.env' \
  --exclude 'hermes/.env' \
  --exclude 'dist/' \
  "$DEPLOY/" "${VM_USER}@${VM_HOST}:${VM_DIR}/"

ssh "${VM_USER}@${VM_HOST}" "chmod +x '$VM_DIR'/scripts/*.sh"

if [[ "$SCRIPTS_ONLY" -eq 0 ]]; then
  scp "$BUNDLE" "${VM_USER}@${VM_HOST}:${VM_DIR}/dist/$(basename "$BUNDLE")"
  if [[ -f "${BUNDLE%.tar}.txt" ]]; then
    scp "${BUNDLE%.tar}.txt" "${VM_USER}@${VM_HOST}:${VM_DIR}/dist/"
  fi
fi

echo "==> Shipped. On the VM ($VM_DIR):"
echo "  cd $VM_DIR"
echo "  cp -n .env.example .env"
echo "  cp -n hermes/.env.example hermes/.env"
if [[ "$SCRIPTS_ONLY" -eq 1 ]]; then
  echo "  ./scripts/start.sh          # images already loaded"
  echo "  ./scripts/load-and-start.sh # load latest tar then start"
else
  echo "  ./scripts/load-and-start.sh dist/$(basename "$BUNDLE")"
fi
echo "Dashboard: http://$VM_HOST/  (also :9119)"
