#!/usr/bin/env bash
set -euo pipefail

# Compatibility alias for the Lab entrypoint.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy-and-start.sh" "$@"
