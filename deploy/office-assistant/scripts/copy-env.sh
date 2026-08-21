#!/usr/bin/env bash
set -euo pipefail

# Copy .env.example files when the destination is missing. Never overwrite an
# existing operator or role env file (.env, models.env, hermes/<role>/.env).

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

created=0

copy_if_missing() {
  local src="$1"
  local dest="$2"
  if [[ -f "$dest" ]]; then
    echo "keep $dest"
    return
  fi
  cp "$src" "$dest"
  echo "created $dest"
  created=$((created + 1))
}

copy_if_missing "$DEPLOY_DIR/.env.example" "$DEPLOY_DIR/.env"
copy_if_missing "$DEPLOY_DIR/models.env.example" "$DEPLOY_DIR/models.env"
for role in $HERMES_ROLES; do
  copy_if_missing \
    "$DEPLOY_DIR/hermes/$role/.env.example" \
    "$DEPLOY_DIR/hermes/$role/.env"
done

if [[ "$created" -gt 0 ]]; then
  echo "Fill secrets in $DEPLOY_DIR/.env, $DEPLOY_DIR/models.env, and each hermes/<role>/.env, then run scripts/deploy-and-start.sh"
fi
