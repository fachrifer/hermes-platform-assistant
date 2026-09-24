#!/usr/bin/env bash
# Role-scoped FAST read helper for Hermes specialists → office-gateway.
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

base="$(office_gw_base)"
token="${OFFICE_GATEWAY_TOKEN:-}"
role="${OFFICE_FAST_ROLE:-}"

if [[ -z "$token" || -z "$role" ]]; then
  echo "error: OFFICE_GATEWAY_TOKEN and OFFICE_FAST_ROLE required" >&2
  exit 2
fi

case "$role" in
  lab-host | vector | cluster-gpu | llm-edge | obs | edge) ;;
  *)
    echo "error: invalid OFFICE_FAST_ROLE: ${role}" >&2
    exit 2
    ;;
esac

gw_get() {
  local path="$1"
  local max="${2:-2}"
  curl -sS --max-time "$max" \
    -H "Authorization: Bearer ${token}" \
    -H "Accept: application/json" \
    "${base}${path}"
}

if ! status_json=$(gw_get /v1/status); then
  exit 1
fi
if [[ -z "$status_json" ]]; then
  exit 1
fi

export OFFICE_FAST_STATUS="$status_json"

case "$role" in
  lab-host)
    OFFICE_FAST_DOCKER=$(gw_get "/v1/docker/containers?all=true&compact=1" || true)
    OFFICE_FAST_NETWORKS=$(gw_get /v1/docker/networks || true)
    export OFFICE_FAST_DOCKER OFFICE_FAST_NETWORKS
    ;;
  cluster-gpu)
    OFFICE_FAST_MIG=$(gw_get /v1/gpu/mig || true)
    OFFICE_FAST_NODES=$(gw_get "/v1/k8s/resources?kind=nodes" || true)
    export OFFICE_FAST_MIG OFFICE_FAST_NODES
    ;;
  llm-edge)
    OFFICE_FAST_LLM=$(gw_get /v1/llm/status 8 || true)
    OFFICE_FAST_HTTPROUTE=$(gw_get "/v1/k8s/resources?kind=httproute" || true)
    OFFICE_FAST_GATEWAY=$(gw_get "/v1/k8s/resources?kind=gateway" || true)
    export OFFICE_FAST_LLM OFFICE_FAST_HTTPROUTE OFFICE_FAST_GATEWAY
    ;;
  obs)
    OFFICE_FAST_GRAFANA=$(gw_get /v1/grafana/links || true)
    export OFFICE_FAST_GRAFANA
    ;;
  edge)
    OFFICE_FAST_ROUTES=$(gw_get /v1/edge/routes || true)
    OFFICE_FAST_TLS=$(gw_get /v1/edge/tls || true)
    export OFFICE_FAST_ROUTES OFFICE_FAST_TLS
    ;;
esac

python3 - "$role" <<'PY'
from __future__ import annotations

import json
import os
import sys
from typing import Optional

CAN = {
    "lab-host": (
        "Lab Docker inspect; chat restart allowlist after APPROVE; "
        "autoheal any non-deny container if armed"
    ),
    "vector": (
        "Milvus/Attu health; lab restart after APPROVE; prod read-only; "
        "autoheal is Hephaestus-only"
    ),
    "cluster-gpu": "MIG + k8s nodes read-only",
    "llm-edge": "LiteLLM health; HTTPRoute/Gateway counts read-only",
    "obs": "Metrics query + Grafana links read-only",
    "edge": (
        "HTTPS path routes + TLS read; apply/restart office-edge after APPROVE only"
    ),
}


def load_env_json(key: str):
    raw = os.environ.get(key, "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def format_endpoints(status: Optional[dict]) -> str:
    services = (status or {}).get("services") if isinstance(status, dict) else None
    if not services:
        return "endpoints: none"
    parts = []
    for svc in services:
        if isinstance(svc, dict) and svc.get("name"):
            parts.append(f"{svc['name']}={svc.get('status', 'unknown')}")
    return "endpoints: " + (",".join(parts) if parts else "none")


def k8s_item_count(data) -> Optional[int]:
    if not isinstance(data, dict) or "items" not in data:
        return None
    items = data.get("items")
    return len(items) if isinstance(items, list) else 0


def gpu_alloc_count(nodes: Optional[dict]) -> int:
    total = 0
    for item in (nodes or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        alloc = (item.get("status") or {}).get("allocatable") or {}
        for key, value in alloc.items():
            if "nvidia.com/gpu" in str(key).lower():
                try:
                    total += int(str(value))
                except ValueError:
                    pass
    return total


def main() -> None:
    role = sys.argv[1]
    lines: list[str] = []
    lines.append(f"can: {CAN[role]}")
    lines.append(format_endpoints(load_env_json("OFFICE_FAST_STATUS")))

    if role == "lab-host":
        docker = load_env_json("OFFICE_FAST_DOCKER")
        if isinstance(docker, dict):
            unhealthy = len(docker.get("unhealthy") or [])
            heal_rows = docker.get("heal_candidates") or []
            if isinstance(heal_rows, list) and heal_rows:
                heal_names = ",".join(str(name) for name in heal_rows[:10])
                heal_line = f"heal_candidates: {heal_names}"
            else:
                heal_line = "heal_candidates: none"
            lines.append(
                "docker: "
                f"running={docker.get('running', 0)} "
                f"total={docker.get('total', 0)} "
                f"exited={docker.get('exited', 0)} "
                f"unhealthy={unhealthy}"
            )
            lines.append(heal_line)
        networks = load_env_json("OFFICE_FAST_NETWORKS")
        if isinstance(networks, dict):
            nets = networks.get("networks") or []
            lines.append(f"networks={len(nets) if isinstance(nets, list) else 0}")

    elif role == "cluster-gpu":
        mig = load_env_json("OFFICE_FAST_MIG")
        if isinstance(mig, dict):
            lines.append(f"mig: {'ok' if mig.get('ok') else 'mismatch'}")
        nodes = load_env_json("OFFICE_FAST_NODES")
        node_count = k8s_item_count(nodes)
        if node_count is None:
            lines.append("k8s=unavailable")
        else:
            lines.append(f"nodes={node_count} gpu_alloc={gpu_alloc_count(nodes)}")

    elif role == "llm-edge":
        llm = load_env_json("OFFICE_FAST_LLM")
        if isinstance(llm, dict):
            if not llm.get("configured"):
                lines.append("litellm: not configured")
            elif llm.get("ok"):
                keys = llm.get("keys")
                keys_part = f" keys={keys}" if keys is not None else ""
                lines.append(f"litellm: ok models={llm.get('models', 0)}{keys_part}")
            else:
                err = llm.get("error") or llm.get("auth") or "error"
                lines.append(f"litellm: {err}")
        hr = k8s_item_count(load_env_json("OFFICE_FAST_HTTPROUTE"))
        gw = k8s_item_count(load_env_json("OFFICE_FAST_GATEWAY"))
        if hr is None and gw is None:
            lines.append("k8s=unavailable")
        else:
            lines.append(f"httproute={0 if hr is None else hr} gateway={0 if gw is None else gw}")

    elif role == "obs":
        grafana = load_env_json("OFFICE_FAST_GRAFANA")
        links = (grafana or {}).get("links") if isinstance(grafana, dict) else None
        if isinstance(links, list):
            for link in links[:3]:
                if isinstance(link, dict) and link.get("name"):
                    lines.append(f"grafana: {link['name']}")

    elif role == "edge":
        routes = load_env_json("OFFICE_FAST_ROUTES")
        route_rows = (routes or {}).get("routes") if isinstance(routes, dict) else None
        if isinstance(route_rows, list):
            names = [
                str(row.get("name"))
                for row in route_rows
                if isinstance(row, dict) and row.get("name")
            ]
            lines.append(f"paths={len(route_rows)}")
            if names:
                lines.append("route: " + " ".join(names[:8]))
        tls = load_env_json("OFFICE_FAST_TLS")
        if isinstance(tls, dict):
            if tls.get("configured"):
                state = "expired" if tls.get("expired") else "ok"
                days = tls.get("days_remaining")
                lines.append(f"tls: {state} days={days}")
            else:
                lines.append("tls: not configured")

    for line in lines[:12]:
        print(line[:120] if len(line) > 120 else line)


if __name__ == "__main__":
    main()
PY
