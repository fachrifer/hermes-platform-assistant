#!/usr/bin/env bash
# Supervisor sync tick: GET /v1/watch/summary → upsert office-watch Kanban card.
set -euo pipefail

_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCH_SUMMARY="${OFFICE_WATCH_SUMMARY:-${_script_dir}/office-watch-summary.sh}"
KANBAN_PY="${OFFICE_WATCH_KANBAN:-${_script_dir}/office-watch-kanban.py}"
KANBAN_DIR="${OFFICE_KANBAN_DIR:-/opt/data/kanban}"
WATCH_INTERVAL="${OFFICE_WATCH_INTERVAL:-60}"

sync_tick() {
  "$WATCH_SUMMARY" | python3 "$KANBAN_PY" --dir "$KANBAN_DIR"
}

if [[ "${OFFICE_WATCH_ONCE:-}" == "1" ]]; then
  sync_tick || exit 1
  exit 0
fi

while true; do
  sync_tick || echo "office-watch-sync: summary/kanban tick failed" >&2
  sleep "$WATCH_INTERVAL"
done
