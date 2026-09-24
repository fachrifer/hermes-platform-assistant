#!/usr/bin/env bash
# One-shot POST /v1/actions/propose (and execute) for Hermes specialists.
# Usage:
#   office-gw-propose.sh restart_service TARGET
#   office-gw-propose.sh apply_edge_routes add NAME PATH UPSTREAM WS STRIP
#   office-gw-propose.sh apply_edge_routes          # full edge-routes on stdin
#   office-gw-propose.sh execute ACTION_ID
set -euo pipefail

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

cmd="${1:-}"
token="${OFFICE_GATEWAY_TOKEN:-}"
if [[ -z "$token" ]]; then
  echo "error: OFFICE_GATEWAY_TOKEN is unset" >&2
  exit 2
fi
if [[ -z "$cmd" ]]; then
  echo "usage: office-gw-propose.sh restart_service TARGET | apply_edge_routes [add NAME PATH UPSTREAM WS STRIP] | execute ACTION_ID" >&2
  exit 2
fi

base="$(office_gw_base)"

gw_curl() {
  curl -sS --max-time 10 \
    -H "Authorization: Bearer ${token}" \
    -H "Accept: application/json" \
    "$@"
}

print_result() {
  python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    sys.stdout.write(raw)
    sys.exit(0)
print(json.dumps(data, separators=(",", ":")))
aid = data.get("action_id") or ""
if aid:
    print("APPROVE " + aid)
'
}

case "$cmd" in
  restart_service)
    target="${2:-}"
    if [[ -z "$target" ]]; then
      echo "usage: office-gw-propose.sh restart_service TARGET" >&2
      exit 2
    fi
    body="$(python3 -c 'import json,sys; print(json.dumps({"action":"restart_service","target":sys.argv[1],"auto_execute":False}))' "$target")"
    gw_curl -H "Content-Type: application/json" -d "$body" "${base}/v1/actions/propose" | print_result
    ;;
  apply_edge_routes)
    if [[ "${2:-}" == "add" ]]; then
      name="${3:-}"
      path="${4:-}"
      upstream="${5:-}"
      ws="${6:-}"
      strip="${7:-}"
      if [[ -z "$name" || -z "$path" || -z "$upstream" || -z "$ws" || -z "$strip" ]]; then
        echo "usage: office-gw-propose.sh apply_edge_routes add NAME PATH UPSTREAM WS STRIP" >&2
        exit 2
      fi
      if [[ "$ws" != "0" && "$ws" != "1" ]] || [[ "$strip" != "0" && "$strip" != "1" ]]; then
        echo "error: WS and STRIP must be 0 or 1" >&2
        exit 2
      fi
      routes_json="$(gw_curl "${base}/v1/edge/routes")"
      body="$(python3 -c '
import json, sys
name, path, upstream, ws, strip = sys.argv[1:6]
data = json.loads(sys.stdin.read())
text = data.get("text") or ""
if text and not text.endswith("\n"):
    text += "\n"
text += f"{name} {path} {upstream} {ws} {strip}\n"
print(json.dumps({
    "action": "apply_edge_routes",
    "target": "edge-routes",
    "params": {"content": text},
    "auto_execute": False,
}))
' "$name" "$path" "$upstream" "$ws" "$strip" <<<"$routes_json")"
      gw_curl -H "Content-Type: application/json" -d "$body" "${base}/v1/actions/propose" | print_result
    else
      content="$(cat)"
      body="$(python3 -c 'import json,sys; print(json.dumps({"action":"apply_edge_routes","target":"edge-routes","params":{"content":sys.stdin.read()},"auto_execute":False}))' <<<"$content")"
      gw_curl -H "Content-Type: application/json" -d "$body" "${base}/v1/actions/propose" | print_result
    fi
    ;;
  execute)
    action_id="${2:-}"
    if [[ -z "$action_id" ]]; then
      echo "usage: office-gw-propose.sh execute ACTION_ID" >&2
      exit 2
    fi
    body="$(python3 -c 'import json,sys; print(json.dumps({"action_id":sys.argv[1]}))' "$action_id")"
    gw_curl -H "Content-Type: application/json" -d "$body" "${base}/v1/actions/execute" | print_result
    ;;
  *)
    echo "usage: office-gw-propose.sh restart_service TARGET | apply_edge_routes [add NAME PATH UPSTREAM WS STRIP] | execute ACTION_ID" >&2
    exit 2
    ;;
esac
