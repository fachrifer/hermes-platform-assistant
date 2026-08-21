#!/usr/bin/env bash
set -euo pipefail

# Pair office-gateway and A2A tokens so specialists match the operator .env.
# Does not print secret values.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

python3 - "$DEPLOY_DIR" <<'PY'
from pathlib import Path
import sys

deploy = Path(sys.argv[1])
gateway_map = {
    "supervisor": "OFFICE_GATEWAY_TOKEN_SUPERVISOR",
    "lab-host": "OFFICE_GATEWAY_TOKEN_LAB_HOST",
    "vector": "OFFICE_GATEWAY_TOKEN_VECTOR",
    "cluster-gpu": "OFFICE_GATEWAY_TOKEN_CLUSTER",
    "llm-edge": "OFFICE_GATEWAY_TOKEN_LLM",
    "obs": "OFFICE_GATEWAY_TOKEN_OBS",
}
a2a_map = {
    "lab-host": "A2A_TOKEN_LAB_HOST",
    "vector": "A2A_TOKEN_VECTOR",
    "cluster-gpu": "A2A_TOKEN_CLUSTER_GPU",
    "llm-edge": "A2A_TOKEN_LLM_EDGE",
    "obs": "A2A_TOKEN_OBS",
}


def load(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def upsert(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    seen: set[str] = set()
    out = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            out.append(line)
            continue
        key, current = line.split("=", 1)
        if key in values:
            out.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")


root = load(deploy / ".env")
supervisor = load(deploy / "hermes" / "supervisor" / ".env")
missing = [key for key in gateway_map.values() if not root.get(key)]
if missing:
    raise SystemExit("pair-env: missing " + ", ".join(missing) + " in .env")
missing_a2a = [key for key in a2a_map.values() if not supervisor.get(key)]
if missing_a2a:
    raise SystemExit(
        "pair-env: missing " + ", ".join(missing_a2a) + " in hermes/supervisor/.env"
    )

upsert(
    deploy / "hermes" / "supervisor" / ".env",
    {"OFFICE_GATEWAY_TOKEN": root[gateway_map["supervisor"]]},
)
for role, env_key in gateway_map.items():
    if role == "supervisor":
        continue
    upsert(
        deploy / "hermes" / role / ".env",
        {
            "OFFICE_GATEWAY_TOKEN": root[env_key],
            "A2A_BEARER_TOKEN": supervisor[a2a_map[role]],
        },
    )
print("paired office-gateway and A2A tokens across hermes role .env files")
PY
