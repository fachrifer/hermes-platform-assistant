#!/usr/bin/env bash
set -euo pipefail

# Issue office-console TLS from the Lab Internal CA (IP SAN).
# Kept as the historic entry point used by deploy-and-start / local-up.
# CA private key stays in CA_DIR (not in the nginx certs mount).

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

"$SCRIPT_DIR/init-lab-ca.sh"
"$SCRIPT_DIR/issue-edge-cert.sh"
