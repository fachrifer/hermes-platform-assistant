#!/usr/bin/env bash
# Load the latest (or given) image tar, then start office-gateway + Hermes Dashboard.
# Run on the VM:
#   cd /home/timai/hermes-assistant
#   ./scripts/load-and-start.sh
#   ./scripts/load-and-start.sh dist/office-assistant-images-YYYYMMDDTHHMMSSZ.tar
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/load-images.sh" "$@"
"$SCRIPT_DIR/start.sh"
