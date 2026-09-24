#!/usr/bin/env bash
set -euo pipefail

# Run `hermes doctor` in every fleet Hermes container. Use this when Dashboard
# Chat / agent start is slow: doctor reports MCP, browser/npx, provider, and
# state.db issues that stall session build.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

cd "$DEPLOY_DIR"

SERVICES=(
  hermes-agent
  hermes-lab-host
  hermes-vector
  hermes-cluster-gpu
  hermes-llm-edge
  hermes-obs
  hermes-edge
)

echo "hermes doctor — office fleet"
echo "Look for: MCP servers, agent-browser/npx, Playwright Chromium,"
echo "provider/API key, Node.js, and state.db warnings."
echo

failed=0
for svc in "${SERVICES[@]}"; do
  echo "======== ${svc} ========"
  if ! office_compose exec -T "$svc" hermes doctor; then
    echo "error: hermes doctor failed in ${svc}" >&2
    failed=1
  fi
  echo
done

if [[ "$failed" -ne 0 ]]; then
  exit 1
fi
