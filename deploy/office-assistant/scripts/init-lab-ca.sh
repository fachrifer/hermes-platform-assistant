#!/usr/bin/env bash
set -euo pipefail

# Create the Lab Internal CA once on the host. The CA private key stays in
# CA_DIR (default deploy/ca) and is never mounted into office-console.
#
# Env:
#   CA_DIR    default: ../ca relative to this script's deploy root
#   CA_DAYS   default: 3650 (~10 years)
#   FORCE     set to 1 to overwrite an existing CA (breaks already-trusted clients)

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

CA_DIR="${CA_DIR:-$DEPLOY_DIR/ca}"
CA_DAYS="${CA_DAYS:-3650}"
FORCE="${FORCE:-0}"

mkdir -p "$CA_DIR"

if [[ -f "$CA_DIR/ca.crt" && -f "$CA_DIR/ca.key" && "$FORCE" != "1" ]]; then
  echo "CA already present in $CA_DIR (set FORCE=1 to regenerate)"
  exit 0
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "error: openssl is required to create the Lab Internal CA" >&2
  exit 1
fi

TMP_CFG="$(mktemp)"
trap 'rm -f "$TMP_CFG"' EXIT
cat >"$TMP_CFG" <<EOF
[req]
default_bits = 4096
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_ca

[dn]
CN = Lab Internal CA
O = Office Assistant Lab

[v3_ca]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
EOF

openssl req -x509 -newkey rsa:4096 -nodes -days "$CA_DAYS" \
  -keyout "$CA_DIR/ca.key" \
  -out "$CA_DIR/ca.crt" \
  -config "$TMP_CFG" \
  -extensions v3_ca

chmod 600 "$CA_DIR/ca.key"
chmod 644 "$CA_DIR/ca.crt"
echo "wrote $CA_DIR/ca.crt and $CA_DIR/ca.key (CN=Lab Internal CA, ${CA_DAYS}d)"
echo "keep ca.key on the host only; import ca.crt into client trust stores once"
