#!/usr/bin/env bash
# On your PC (has internet + Docker): build linux/amd64 images and copy them to the VM.
# Does not overwrite .env or hermes/.env on the VM.
#
#   ./deploy/office-assistant/scripts/build-and-ship.sh
#
# Optional:
#   OFFICE_VM_HOST=10.216.4.80 OFFICE_VM_USER=timai \
#   OFFICE_VM_DIR=/home/timai/hermes-assistant \
#   ./deploy/office-assistant/scripts/build-and-ship.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Build + pack linux/amd64 images"
"$SCRIPT_DIR/export-images.sh"

echo "==> Copy configs, scripts, and image tar to the VM"
"$SCRIPT_DIR/ship-to-vm.sh"
