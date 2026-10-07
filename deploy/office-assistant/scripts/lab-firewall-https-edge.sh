#!/usr/bin/env bash
set -euo pipefail

# Lab VM firewall helper for the HTTPS edge proxy.
# Default: print planned rules and exit 0 (dry-run).
# Apply:    LAB_FIREWALL_APPLY=1 ./scripts/lab-firewall-https-edge.sh
#
# Keeps SSH :22, HTTP/HTTPS :80/:443 (AI platform) and the Hermes console :9443 reachable.
# Rejects NEW inbound TCP to common Lab raw publish ports so services
# are only reached via https://10.216.4.80/<path>/.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

APPLY="${LAB_FIREWALL_APPLY:-0}"
# Common Lab publishes from the estate inventory (HTTP UIs/APIs + Milvus/Qdrant).
RAW_PORTS=(555 8000 3001 8001 8010 9119 19530 2379 6333 6334 9091)

echo "Lab HTTPS edge firewall plan"
echo "  allow: TCP 22, 80, 443, 9443"
echo "  reject NEW: TCP ${RAW_PORTS[*]}"
echo "  apply: LAB_FIREWALL_APPLY=1 (currently APPLY=${APPLY})"

if [[ "$APPLY" != "1" ]]; then
  echo "dry-run only; no rules changed"
  exit 0
fi

if ! office_is_lab_tree && [[ "${LAB_FIREWALL_FORCE:-0}" != "1" ]]; then
  echo "error: refusing to apply firewall outside the Lab tree." >&2
  echo "On the Lab VM under /home/timai/hermes-assistant, or set LAB_FIREWALL_FORCE=1." >&2
  exit 1
fi

reject_port() {
  local port="$1"
  if command -v nft >/dev/null 2>&1 && nft list tables 2>/dev/null | grep -q 'table inet filter'; then
    # Best-effort: add a reject rule if an inet filter input chain exists.
    nft add rule inet filter input tcp dport "$port" ct state new reject 2>/dev/null \
      || nft insert rule inet filter input tcp dport "$port" reject 2>/dev/null \
      || echo "warning: could not add nft rule for :${port}" >&2
    return 0
  fi
  if command -v iptables >/dev/null 2>&1; then
    if ! iptables -C INPUT -p tcp --dport "$port" -m conntrack --ctstate NEW -j REJECT 2>/dev/null; then
      iptables -I INPUT -p tcp --dport "$port" -m conntrack --ctstate NEW -j REJECT
    fi
    return 0
  fi
  echo "error: neither usable nftables inet filter nor iptables found" >&2
  return 1
}

ensure_allow() {
  local port="$1"
  if command -v iptables >/dev/null 2>&1; then
    if ! iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
      iptables -I INPUT -p tcp --dport "$port" -j ACCEPT
    fi
  fi
}

ensure_allow 22
ensure_allow 80
ensure_allow 443
ensure_allow 9443

for port in "${RAW_PORTS[@]}"; do
  reject_port "$port"
done

echo "applied: raw Lab ports rejected for NEW connections; 22/80/443/9443 allowed"
echo "verify from another host: curl -vk https://10.216.4.80/ and curl --connect-timeout 2 http://10.216.4.80:555/ || true"
