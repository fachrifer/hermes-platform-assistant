from __future__ import annotations

ROLES = frozenset(
    {"supervisor", "lab-host", "vector", "cluster-gpu", "llm-edge", "obs", "edge"}
)

WATCH_SPECIALIST_ROLES = frozenset(
    {"lab-host", "vector", "cluster-gpu", "llm-edge", "obs", "edge"}
)

WATCH_STALE_SECONDS = 180

READ_ROUTES = {
    "supervisor": frozenset(
        {
            "/v1/status",
            "/v1/services",
            "/v1/audit",
            "/v1/metrics/query",
            "/v1/grafana/links",
            "/v1/gpu/mig",
            "/v1/host/gpu",
            "/v1/fleet",
            "/v1/watch",
            "/v1/llm",
            "/v1/litellm",
        }
    ),
    "lab-host": frozenset({"/v1/status", "/v1/services", "/v1/docker"}),
    "vector": frozenset({"/v1/status", "/v1/services"}),
    "cluster-gpu": frozenset(
        {"/v1/status", "/v1/services", "/v1/k8s/resources", "/v1/gpu/mig", "/v1/host/gpu"}
    ),
    "llm-edge": frozenset(
        {"/v1/status", "/v1/services", "/v1/k8s/resources", "/v1/llm", "/v1/litellm"}
    ),
    "obs": frozenset(
        {"/v1/status", "/v1/services", "/v1/metrics/query", "/v1/grafana/links"}
    ),
    "edge": frozenset({"/v1/status", "/v1/services", "/v1/edge"}),
}

WRITE_ROLES = frozenset({"lab-host", "vector", "edge"})


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


def can_post_watch(role: str) -> bool:
    return role in WATCH_SPECIALIST_ROLES
