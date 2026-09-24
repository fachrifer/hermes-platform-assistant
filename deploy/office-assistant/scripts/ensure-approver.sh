#!/usr/bin/env bash
set -euo pipefail

# Create the approvals-page login (edge/approvers.htpasswd) once.
# The password goes to .local-login (mode 600), never to stdout.
#
#   OFFICE_APPROVER_USER=timai ./scripts/ensure-approver.sh

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

HTPASSWD="$DEPLOY_DIR/edge/approvers.htpasswd"
LOGIN="$DEPLOY_DIR/.local-login"
USER_NAME="${OFFICE_APPROVER_USER:-timai}"

if [[ -d "$HTPASSWD" ]]; then
  rmdir "$HTPASSWD" 2>/dev/null || {
    echo "error: $HTPASSWD is a non-empty directory (left by a bind mount?)" >&2
    exit 1
  }
fi
if [[ -s "$HTPASSWD" ]]; then
  echo "approver login exists (edge/approvers.htpasswd)"
  exit 0
fi
if [[ ! "$USER_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "error: OFFICE_APPROVER_USER must be letters, digits, dot, dash or underscore" >&2
  exit 1
fi

password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
hash="$(printf '%s\n' "$password" | openssl passwd -apr1 -stdin)"
mkdir -p "$(dirname "$HTPASSWD")"
printf '%s:%s\n' "$USER_NAME" "$hash" > "$HTPASSWD"
chmod 644 "$HTPASSWD"

touch "$LOGIN"
chmod 600 "$LOGIN"
python3 - "$LOGIN" "$USER_NAME" "$password" <<'PY'
import sys
from pathlib import Path

path, user, password = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
rows = [
    line for line in path.read_text().splitlines()
    if not line.startswith(("approver_user=", "approver_password="))
]
rows += [f"approver_user={user}", f"approver_password={password}"]
path.write_text("\n".join(rows) + "\n")
PY
echo "approver login stored in .local-login (user $USER_NAME)"
