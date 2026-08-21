from __future__ import annotations

FLEET_AGENTS = (
    {
        "id": "supervisor",
        "name": "Athena",
        "fate_class": "Ruler",
        "role": "orchestrator",
        "purpose": "Orchestrator",
        "avatar": "/avatars/athena-ruler.png",
        "ready_service": "gateway",
        "service_names": ("gateway",),
    },
    {
        "id": "lab-host",
        "name": "Hephaestus",
        "fate_class": "Archer",
        "role": "lab-host",
        "purpose": "Lab host",
        "avatar": "/avatars/hephaestus-archer.png",
        "ready_service": "hermes-lab-host",
        "service_names": ("aiplatform", "common-service"),
    },
    {
        "id": "vector",
        "name": "Mnemosyne",
        "fate_class": "Caster",
        "role": "vector",
        "purpose": "Vector store",
        "avatar": "/avatars/mnemosyne-caster.png",
        "ready_service": "hermes-vector",
        "service_names": ("milvus", "minio", "attu", "qdrant"),
    },
    {
        "id": "cluster-gpu",
        "name": "Surtr",
        "fate_class": "Berserker",
        "role": "cluster-gpu",
        "purpose": "GPU cluster",
        "avatar": "/avatars/surtr-berserker.png",
        "ready_service": "hermes-cluster-gpu",
        "service_names": ("rancher",),
    },
    {
        "id": "llm-edge",
        "name": "Iris",
        "fate_class": "Rider",
        "role": "llm-edge",
        "purpose": "LLM edge",
        "avatar": "/avatars/iris-rider.png",
        "ready_service": "hermes-llm-edge",
        "service_names": ("litellm",),
    },
    {
        "id": "obs",
        "name": "Argus",
        "fate_class": "Watcher",
        "role": "obs",
        "purpose": "Observability",
        "avatar": "/avatars/argus-watcher.png",
        "ready_service": "hermes-obs",
        "service_names": ("grafana", "victoria", "prometheus"),
    },
)


def _status_for(agent: dict, services: list[dict]) -> str:
    ready = agent.get("ready_service")
    if ready:
        hit = next((item for item in services if item.get("name") == ready), None)
        if hit is None:
            return "unknown"
        status = hit.get("status") or "unknown"
        if status == "ok":
            return "ok"
        if status == "critical":
            return "critical"
        if status == "unknown":
            return "unknown"
        return "warn"
    names = set(agent["service_names"])
    matched = [item for item in services if item.get("name") in names]
    if not matched:
        return "unknown"
    if any(item.get("status") == "critical" for item in matched):
        return "critical"
    if any(item.get("status") != "ok" for item in matched):
        return "warn"
    return "ok"


def build_brief(
    *,
    services: list[dict],
    mig: dict | None = None,
    grafana_links: list[dict] | None = None,
) -> dict:
    agents = []
    for agent in FLEET_AGENTS:
        row = {
            "id": agent["id"],
            "name": agent["name"],
            "fate_class": agent["fate_class"],
            "role": agent["role"],
            "purpose": agent["purpose"],
            "avatar": agent["avatar"],
            "status": _status_for(agent, services),
        }
        agents.append(row)
    activity = _activity_for(agents)
    return {
        "agents": agents,
        "activity": activity,
        "services": services,
        "mig": mig or {},
        "grafana": grafana_links or [],
    }


def _activity_for(agents: list[dict]) -> list[dict]:
    lines = []
    for agent in agents:
        if agent["id"] == "supervisor":
            continue
        status = agent["status"]
        name = agent["name"]
        if status == "critical":
            lines.append(
                {
                    "kind": "talk",
                    "text": f"Something looks off. I'll check with {name}.",
                    "target": name,
                }
            )
        elif status == "warn":
            lines.append(
                {
                    "kind": "talk",
                    "text": f"Give me a second — I'm asking {name}.",
                    "target": name,
                }
            )
    if lines:
        return lines[:4]
    return [{"kind": "listen", "text": "Whenever you're ready.", "target": None}]
