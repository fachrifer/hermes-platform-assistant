#!/usr/bin/env bash
set -euo pipefail

# Create the approvals-page login (edge/approvers.htpasswd) once.
# The password goes to .local-login (mode 600), never to stdout.
# To change it: edit approver_password in .local-login, re-run this script,
# then restart office-edge.
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
login_value() {
  if [[ -f "$LOGIN" ]]; then
    sed -n "s/^$1=//p" "$LOGIN" | tail -n 1
  fi
}

if [[ -s "$HTPASSWD" ]]; then
  want_user="$(login_value approver_user)"
  want_password="$(login_value approver_password)"
  current_hash=""
  if [[ -n "$want_user" ]]; then
    current_hash="$(awk -F: -v u="$want_user" '$1 == u {print substr($0, length(u) + 2); exit}' "$HTPASSWD")"
  fi
  in_sync=false
  if [[ -z "$want_user" || -z "$want_password" ]]; then
    in_sync=true
  elif [[ "$current_hash" == '$apr1$'* ]]; then
    salt="$(cut -d'$' -f3 <<<"$current_hash")"
    [[ "$(printf '%s\n' "$want_password" | openssl passwd -apr1 -salt "$salt" -stdin)" == "$current_hash" ]] && in_sync=true
  elif [[ -n "$current_hash" ]]; then
    in_sync=true  # hand-made non-apr1 hash (e.g. bcrypt): leave it alone
  fi
  if [[ "$in_sync" == true ]]; then
    echo "approver login exists (edge/approvers.htpasswd)"
    exit 0
  fi
  if [[ ! "$want_user" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "error: approver_user in .local-login must be letters, digits, dot, dash or underscore" >&2
    exit 1
  fi
  hash="$(printf '%s\n' "$want_password" | openssl passwd -apr1 -stdin)"
  # Rewrite in place: office-edge bind-mounts this single file (a new inode would stay invisible).
  printf '%s:%s\n' "$want_user" "$hash" > "$HTPASSWD"
  echo "approver password updated from .local-login (user $want_user); apply: docker compose restart office-edge"
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
