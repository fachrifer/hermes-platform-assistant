#!/usr/bin/env bash
# Read-only helper for Hermes specialists → office-gateway.
# Usage: office-gw-get.sh /v1/status
#        office-gw-get.sh /v1/edge/tls
set -euo pipefail

path="${1:-}"
if [[ -z "$path" || "$path" != /* ]]; then
  echo "usage: office-gw-get.sh /v1/..." >&2
  exit 2
fi

# Gateway listens on 8080. Bare http://office-gateway is port 80 (connection refused).
office_gw_base() {
  local base="${OFFICE_GATEWAY_URL:-http://office-gateway:8080}"
  base="${base%/}"
  if [[ "$base" =~ ^https?://[^/:]+$ ]]; then
    printf '%s:8080' "$base"
    return
  fi
  if [[ "$base" =~ ^(https?://[^/:]+):80$ ]]; then
    printf '%s:8080' "${BASH_REMATCH[1]}"
    return
  fi
  printf '%s' "$base"
}

base="$(office_gw_base)"
token="${OFFICE_GATEWAY_TOKEN:-}"
if [[ -z "$token" ]]; then
  echo "error: OFFICE_GATEWAY_TOKEN is unset" >&2
  exit 1
fi
exec curl -sS \
  -H "Authorization: Bearer ${token}" \
  -H "Accept: application/json" \
  "${base}${path}"
