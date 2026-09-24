#!/usr/bin/env bash
# Specialist watch tick: office-gw-fast.sh → snapshot file → POST /v1/watch/snapshot.
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

watch_ts() {
  local raw
  raw="$(TZ=Asia/Jakarta date +%Y-%m-%dT%H:%M:%S%z)"
  if [[ "$raw" =~ ^(.+[+-][0-9]{2})([0-9]{2})$ ]]; then
    printf '%s:%s' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
  else
    printf '%s' "$raw"
  fi
}

_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCH_FAST="${OFFICE_WATCH_FAST:-/opt/office/office-gw-fast.sh}"
WATCH_EVAL="${OFFICE_WATCH_EVAL:-${_script_dir}/office-watch-eval.py}"
if [[ ! -f "$WATCH_EVAL" ]]; then
  WATCH_EVAL="/opt/office/office-watch-eval.py"
fi
WATCH_SNAPSHOT="${OFFICE_WATCH_SNAPSHOT:-/opt/data/office-fast-snapshot.txt}"
WATCH_INTERVAL="${OFFICE_WATCH_INTERVAL:-60}"
role="${OFFICE_FAST_ROLE:-}"
token="${OFFICE_GATEWAY_TOKEN:-}"

write_snapshot() {
  local ts="$1"
  local snap_role="$2"
  shift 2
  {
    printf 'ts=%s\n' "$ts"
    printf 'role=%s\n' "$snap_role"
    printf '%s\n' "$@"
  } >"$WATCH_SNAPSHOT"
}

post_snapshot() {
  local ts="$1"
  local snapshot="$2"
  local alert="$3"
  local summary="$4"
  local base
  base="$(office_gw_base)"
  set +e
  printf '%s' "$snapshot" | python3 -c '
import json, sys
snapshot = sys.stdin.read()
ts, alert, summary = sys.argv[1:4]
print(json.dumps({
    "snapshot": snapshot,
    "ts": ts,
    "alert": alert == "true",
    "summary": summary,
}))
' "$ts" "$alert" "$summary" | curl -sS --max-time 5 -X POST \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json" \
    -d @- \
    "${base}/v1/watch/snapshot" >/dev/null
  set -e
}

watch_tick() {
  local ts fast_out fast_rc err_line eval_json alert summary

  if [[ -z "$role" ]]; then
    ts="$(watch_ts)"
    write_snapshot "$ts" "unknown" "error: OFFICE_FAST_ROLE is unset"
    if [[ -n "$token" ]]; then
      post_snapshot "$ts" "$(cat "$WATCH_SNAPSHOT")" true "watch failed"
    fi
    return 1
  fi
  if [[ -z "$token" ]]; then
    ts="$(watch_ts)"
    write_snapshot "$ts" "$role" "error: OFFICE_GATEWAY_TOKEN is unset"
    return 1
  fi

  set +e
  fast_out="$("$WATCH_FAST" 2>&1)"
  fast_rc=$?
  set -e

  ts="$(watch_ts)"

  if [[ "$fast_rc" -ne 0 ]]; then
    err_line="$(printf '%s\n' "$fast_out" | sed -n '1p')"
    [[ -z "$err_line" ]] && err_line="fast helper failed"
    write_snapshot "$ts" "$role" "error: ${err_line}"
    post_snapshot "$ts" "$(cat "$WATCH_SNAPSHOT")" true "watch failed"
    return 0
  fi

  write_snapshot "$ts" "$role" "$fast_out"
  set +e
  eval_json="$(printf '%s' "$fast_out" | python3 "$WATCH_EVAL" "$role")"
  set -e
  alert="$(printf '%s' "$eval_json" | python3 -c 'import json,sys; a=json.load(sys.stdin)["alert"]; print("true" if a else "false")')"
  summary="$(printf '%s' "$eval_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["summary"])')"
  post_snapshot "$ts" "$(cat "$WATCH_SNAPSHOT")" "$alert" "$summary"
}

if [[ "${OFFICE_WATCH_ONCE:-}" == "1" ]]; then
  watch_tick || exit 1
  exit 0
fi

while true; do
  watch_tick || true
  sleep "$WATCH_INTERVAL"
done
