#!/usr/bin/env bash
set -euo pipefail

# Copy .env.example files when the destination is missing. Never overwrite an
# existing operator or role env file.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

copy_if_missing() {
  local src="$1"
  local dest="$2"
  if [[ -f "$dest" ]]; then
    echo "keep $dest"
    return
  fi
  cp "$src" "$dest"
  echo "created $dest"
}

copy_if_missing "$DEPLOY_DIR/.env.example" "$DEPLOY_DIR/.env"
for role in $HERMES_ROLES; do
  copy_if_missing \
    "$DEPLOY_DIR/hermes/$role/.env.example" \
    "$DEPLOY_DIR/hermes/$role/.env"
done

echo "Fill secrets in $DEPLOY_DIR/.env and each hermes/<role>/.env, then run scripts/load-and-start.sh"
