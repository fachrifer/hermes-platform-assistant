#!/usr/bin/env bash
set -euo pipefail

# Issue a short-lived edge leaf cert signed by the Lab Internal CA.
# Writes CERT_DIR/tls.crt (leaf + CA chain) and tls.key for nginx.
# Copies CA public cert to CERT_DIR/ca.crt for operators to distribute.
# CA private key stays in CA_DIR (not in CERT_DIR / not in the container).
#
# Env:
#   CA_DIR         default: ../ca
#   CERT_DIR       default: ../certs
#   TLS_IP         default: 10.216.4.80
#   TLS_EXTRA_IPS  default: 127.0.0.1 (comma-separated extra IP SANs)
#   TLS_DNS        optional comma-separated DNS SANs
#   LEAF_DAYS      default: 90
#   RENEW_IF_DAYS  default: 14 (skip reissue when remaining life is longer)
#   FORCE          set to 1 to reissue even if the current leaf is still valid

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

CA_DIR="${CA_DIR:-$DEPLOY_DIR/ca}"
CERT_DIR="${CERT_DIR:-$DEPLOY_DIR/certs}"
TLS_IP="${TLS_IP:-10.216.4.80}"
TLS_EXTRA_IPS="${TLS_EXTRA_IPS:-127.0.0.1}"
LEAF_DAYS="${LEAF_DAYS:-90}"
RENEW_IF_DAYS="${RENEW_IF_DAYS:-14}"
FORCE="${FORCE:-0}"

if [[ ! -f "$CA_DIR/ca.crt" || ! -f "$CA_DIR/ca.key" ]]; then
  echo "error: Lab Internal CA missing in $CA_DIR — run init-lab-ca.sh first" >&2
  exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "error: openssl is required to issue the edge leaf cert" >&2
  exit 1
fi

mkdir -p "$CERT_DIR"

leaf_still_valid() {
  local crt="$1"
  [[ -f "$crt" && -f "$CERT_DIR/tls.key" ]] || return 1
  openssl verify -CAfile "$CA_DIR/ca.crt" "$crt" >/dev/null 2>&1 || return 1
  openssl x509 -in "$crt" -noout -checkend $((RENEW_IF_DAYS * 86400)) >/dev/null 2>&1
}

if [[ "$FORCE" != "1" ]] && leaf_still_valid "$CERT_DIR/tls.crt"; then
  echo "leaf cert still valid in $CERT_DIR (set FORCE=1 to reissue)"
  cp "$CA_DIR/ca.crt" "$CERT_DIR/ca.crt"
  chmod 644 "$CERT_DIR/ca.crt"
  exit 0
fi

SAN_IPS=("$TLS_IP")
IFS=',' read -r -a extra <<< "$TLS_EXTRA_IPS"
for ip in "${extra[@]}"; do
  ip="$(echo "$ip" | tr -d '[:space:]')"
  [[ -z "$ip" || "$ip" == "$TLS_IP" ]] && continue
  SAN_IPS+=("$ip")
done

ALT=""
idx=1
for ip in "${SAN_IPS[@]}"; do
  ALT="${ALT}IP.${idx} = ${ip}"$'\n'
  idx=$((idx + 1))
done
if [[ -n "${TLS_DNS:-}" ]]; then
  IFS=',' read -r -a dns_names <<< "$TLS_DNS"
  dns_idx=1
  for name in "${dns_names[@]}"; do
    name="$(echo "$name" | tr -d '[:space:]')"
    [[ -z "$name" ]] && continue
    ALT="${ALT}DNS.${dns_idx} = ${name}"$'\n'
    dns_idx=$((dns_idx + 1))
  done
fi

TMP_CFG="$(mktemp)"
TMP_CSR="$(mktemp)"
TMP_LEAF="$(mktemp)"
trap 'rm -f "$TMP_CFG" "$TMP_CSR" "$TMP_LEAF"' EXIT
cat >"$TMP_CFG" <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
req_extensions = v3_req

[dn]
CN = ${TLS_IP}

[v3_req]
subjectAltName = @alt_names
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash

[alt_names]
${ALT}
EOF

openssl genrsa -out "$CERT_DIR/tls.key" 2048
openssl req -new -key "$CERT_DIR/tls.key" -out "$TMP_CSR" -config "$TMP_CFG"
openssl x509 -req -in "$TMP_CSR" \
  -CA "$CA_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" -CAcreateserial \
  -CAserial "$CA_DIR/ca.srl" \
  -out "$TMP_LEAF" -days "$LEAF_DAYS" -sha256 \
  -extfile "$TMP_CFG" -extensions v3_req

cat "$TMP_LEAF" "$CA_DIR/ca.crt" > "$CERT_DIR/tls.crt"
cp "$CA_DIR/ca.crt" "$CERT_DIR/ca.crt"
chmod 640 "$CERT_DIR/tls.key"
chmod 644 "$CERT_DIR/tls.crt" "$CERT_DIR/ca.crt"
echo "wrote $CERT_DIR/tls.crt (leaf+CA chain) and $CERT_DIR/tls.key (${LEAF_DAYS}d, IP SAN ${SAN_IPS[*]})"
echo "distribute $CERT_DIR/ca.crt (or $CA_DIR/ca.crt) to client trust stores once"
