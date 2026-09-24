#!/command/with-contenv sh
# Seed Athena's fleet cron jobs. Must run as hermes — root-owned
# jobs.json makes the gateway ticker fail with Permission denied.
set -e

ln -sf /opt/hermes/.venv/bin/hermes /usr/local/bin/hermes

as_hermes() {
  if command -v s6-setuidgid >/dev/null 2>&1; then
    s6-setuidgid hermes "$@"
  else
    "$@"
  fi
}

seed_job() {
  PROMPT="$1"
  NAME="$2"
  SCHEDULE="$3"
  if [ ! -f "$PROMPT" ]; then
    return 0
  fi
  PROMPT_TEXT=$(cat "$PROMPT")
  # --continuity (v0.21.0): inject the previous run's output so nightly
  # reports can dedupe instead of re-stating the same FAST snapshot.
  if as_hermes hermes cron list 2>/dev/null | grep -Fqi "$NAME"; then
    as_hermes hermes cron edit "$NAME" --schedule "$SCHEDULE" --prompt "$PROMPT_TEXT" --deliver local --continuity || true
  else
    as_hermes hermes cron create "$SCHEDULE" "$PROMPT_TEXT" --name "$NAME" --deliver local --continuity || true
  fi
}

seed_job /opt/office/cron.fleet-check.prompt.txt "nightly fleet check" "0 1 * * *"

# Hourly fleet is disabled: 60s specialist watch already covers FAST snapshots
# without six LLM A2A turns. Remove a previously seeded job on every boot.
as_hermes hermes cron remove "hourly fleet" || true
