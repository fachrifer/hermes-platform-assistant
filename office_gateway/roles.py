from __future__ import annotations

import hmac

AGENT_ROLES = frozenset(
    {"supervisor", "lab-host", "ingress", "llm", "cluster-gpu", "vector", "obs"}
)
APPROVER_ROLE = "approver"
ROLES = AGENT_ROLES | {APPROVER_ROLE}
SPECIALIST_ROLES = AGENT_ROLES - {"supervisor"}

_COMMON = frozenset({"/v1/status", "/v1/services"})

READ_ROUTES: dict[str, frozenset[str]] = {
    "supervisor": _COMMON | {"/v1/fleet"},
    "lab-host": _COMMON | {"/v1/docker", "/v1/actions"},
    "ingress": _COMMON | {"/v1/edge", "/v1/docker/logs", "/v1/actions"},
    "llm": _COMMON | {"/v1/llm"},
    "cluster-gpu": _COMMON | {"/v1/k8s/resources", "/v1/gpu/mig", "/v1/host/gpu"},
    "vector": _COMMON,
    "obs": _COMMON | {"/v1/metrics/query", "/v1/grafana/links", "/v1/actions"},
    APPROVER_ROLE: frozenset({"/v1/status", "/v1/approvals", "/v1/audit"}),
}

WRITE_ACTIONS: dict[str, frozenset[str]] = {
    "lab-host": frozenset({"restart_service"}),
    "ingress": frozenset({"apply_edge_routes", "rollback_edge_routes"}),
    "obs": frozenset(),
}

WRITE_ROLES = frozenset(role for role, actions in WRITE_ACTIONS.items() if actions)


def role_for_token(config, token: str) -> str | None:
    if not token:
        return None
    for role, expected in config.tokens.items():
        if expected and hmac.compare_digest(token.encode(), expected.encode()):
            return role
    return None


def can_read(role: str, route_prefix: str) -> bool:
    allowed = READ_ROUTES.get(role, frozenset())
    return any(route_prefix == p or route_prefix.startswith(p + "/") for p in allowed)


def can_write(role: str) -> bool:
    return role in WRITE_ROLES


def can_propose(role: str, action: str) -> bool:
    return action in WRITE_ACTIONS.get(role, frozenset())
