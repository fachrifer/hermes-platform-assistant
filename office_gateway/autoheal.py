from __future__ import annotations

from office_gateway.docker_ops import normalize_container_status

_HEAL_CANDIDATE_CAP = 10
_NOT_RUNNING_CAP = 20


def is_autoheal_denied(
    name: str,
    compose_service: str = "",
    extra_deny: frozenset[str] = frozenset(),
) -> bool:
    blob = f"{name} {compose_service}".lower()
    if "hermes" in blob:
        return True
    if "office-gateway" in blob:
        return True
    lowered = {item.lower() for item in extra_deny}
    return name.lower() in lowered or compose_service.lower() in lowered


def is_faulted(status: str, health: str | None) -> bool:
    st = (status or "").lower()
    hz = (health or "").lower()
    if "unhealthy" in st or "unhealthy" in hz:
        return True
    return st in {"exited", "dead"}


def compact_listing(
    containers: list[dict], extra_deny: frozenset[str] = frozenset()
) -> dict:
    running = 0
    exited = 0
    unhealthy: list[str] = []
    not_running: list[str] = []
    heal: list[str] = []
    for row in containers:
        name = str(row.get("name") or "")
        status = str(row.get("status") or "")
        health = row.get("health")
        service = str(row.get("compose_service") or "")
        status, parsed_health = normalize_container_status(status)
        if health is None and parsed_health:
            health = parsed_health
        st = status.lower()
        if st == "running":
            running += 1
        if st in {"exited", "dead"}:
            exited += 1
        if "unhealthy" in st or "unhealthy" in str(health or "").lower():
            unhealthy.append(name)
        if st != "running":
            not_running.append(name)
        if is_faulted(status, health if isinstance(health, str) else None) and not is_autoheal_denied(
            name, service, extra_deny
        ):
            heal.append(name)
    return {
        "running": running,
        "total": len(containers),
        "exited": exited,
        "unhealthy": unhealthy,
        "not_running": not_running[:_NOT_RUNNING_CAP],
        "heal_candidates": heal[:_HEAL_CANDIDATE_CAP],
        "truncated": len(containers) > 500,
    }
