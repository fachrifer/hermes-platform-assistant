#!/usr/bin/env bash
# Supervisor helper: GET /v1/watch/summary from office-gateway.
set -euo pipefail

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
exec curl -sS --fail --max-time 5 \
  -H "Authorization: Bearer ${token}" \
  -H "Accept: application/json" \
  "${base}/v1/watch/summary"
