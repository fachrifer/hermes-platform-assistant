from __future__ import annotations

ROLES = frozenset(
    {"supervisor", "lab-host", "vector", "cluster-gpu", "llm-edge", "obs"}
)

READ_ROUTES = {
    "supervisor": frozenset(
        {
            "/v1/status",
            "/v1/services",
            "/v1/audit",
            "/v1/metrics/query",
            "/v1/grafana/links",
        }
    ),
    "lab-host": frozenset({"/v1/status", "/v1/services", "/v1/docker/inspect"}),
    "vector": frozenset({"/v1/status", "/v1/services"}),
    "cluster-gpu": frozenset(
        {"/v1/status", "/v1/services", "/v1/k8s/resources", "/v1/gpu/mig"}
    ),
    "llm-edge": frozenset({"/v1/status", "/v1/services", "/v1/k8s/resources"}),
    "obs": frozenset(
        {"/v1/status", "/v1/services", "/v1/metrics/query", "/v1/grafana/links"}
    ),
}

WRITE_ROLES = frozenset({"lab-host", "vector"})


def role_for_token(config, token: str) -> str | None:
    for role, expected in config.tokens.items():
        if expected and token == expected:
            return role
    return None


def can_read(role: str, route_prefix: str) -> bool:
    allowed = READ_ROUTES.get(role, frozenset())
    return any(route_prefix == p or route_prefix.startswith(p + "/") for p in allowed)


def can_write(role: str) -> bool:
    return role in WRITE_ROLES
