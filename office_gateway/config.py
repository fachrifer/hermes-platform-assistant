from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from office_gateway.roles import ROLES

_SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_TOKEN_ENV = {
    "supervisor": "OFFICE_GATEWAY_TOKEN_SUPERVISOR",
    "lab-host": "OFFICE_GATEWAY_TOKEN_LAB_HOST",
    "vector": "OFFICE_GATEWAY_TOKEN_VECTOR",
    "cluster-gpu": "OFFICE_GATEWAY_TOKEN_CLUSTER",
    "llm-edge": "OFFICE_GATEWAY_TOKEN_LLM",
    "obs": "OFFICE_GATEWAY_TOKEN_OBS",
}


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} wajib diisi")
    return value


_DEFAULT_SERVICE_URLS = (
    "gateway=http://office-gateway:8080/health,"
    "hermes-lab-host=http://hermes-lab-host:9900/.well-known/agent.json,"
    "hermes-vector=http://hermes-vector:9900/.well-known/agent.json,"
    "hermes-cluster-gpu=http://hermes-cluster-gpu:9900/.well-known/agent.json,"
    "hermes-llm-edge=http://hermes-llm-edge:9900/.well-known/agent.json,"
    "hermes-obs=http://hermes-obs:9900/.well-known/agent.json"
)


def _parse_service_urls(value: str) -> dict[str, str]:
    endpoints: dict[str, str] = {}
    for item in (part.strip() for part in value.split(",") if part.strip()):
        name, separator, url = item.partition("=")
        if not separator or not _SERVICE_NAME_RE.fullmatch(name) or not url.startswith(
            ("http://", "https://")
        ):
            raise ValueError("OFFICE_SERVICE_URLS berisi service atau URL tidak valid")
        endpoints[name] = url
    return endpoints


def _service_urls(value: str) -> dict[str, str]:
    endpoints = {**_parse_service_urls(_DEFAULT_SERVICE_URLS), **_parse_service_urls(value)}
    if not endpoints:
        raise ValueError("OFFICE_SERVICE_URLS wajib memiliki minimal satu service")
    return endpoints


def _csv_set(value: str) -> frozenset[str]:
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def _vector_env(value: str) -> dict[str, str]:
    """Parse `name:dev|name:prod`."""
    if not value.strip():
        return {}
    out: dict[str, str] = {}
    for item in (part.strip() for part in value.split(",") if part.strip()):
        name, sep, env = item.partition(":")
        if not sep or env not in {"dev", "prod"} or not _SERVICE_NAME_RE.fullmatch(name):
            raise ValueError("OFFICE_VECTOR_ENV format name:dev|prod tidak valid")
        out[name] = env
    return out


def _grafana_dashboards(value: str) -> dict[str, str]:
    """Parse `name=uid,...`."""
    if not value.strip():
        return {}
    out: dict[str, str] = {}
    for item in (part.strip() for part in value.split(",") if part.strip()):
        name, sep, uid = item.partition("=")
        if not sep or not name or not uid:
            raise ValueError("OFFICE_GRAFANA_DASHBOARDS format name=uid tidak valid")
        out[name] = uid
    return out


def _mig_expected(value: str) -> dict[str, int]:
    """Parse `1g.18gb:7,2g.35gb:2,3g.71gb:2,4g.71gb:1,7g.141gb:1`."""
    if not value.strip():
        return {
            "1g.18gb": 7,
            "2g.35gb": 2,
            "3g.71gb": 2,
            "4g.71gb": 1,
            "7g.141gb": 1,
        }
    out: dict[str, int] = {}
    for item in (part.strip() for part in value.split(",") if part.strip()):
        profile, sep, count = item.partition(":")
        if not sep:
            raise ValueError("OFFICE_MIG_EXPECTED format profile:count tidak valid")
        out[profile] = int(count)
    return out


@dataclass(frozen=True)
class GatewayConfig:
    tokens: dict[str, str]
    service_urls: dict[str, str]
    write_targets: dict[str, frozenset[str]] = field(default_factory=dict)
    vector_env: dict[str, str] = field(default_factory=dict)
    grafana_base_url: str = ""
    grafana_dashboards: dict[str, str] = field(default_factory=dict)
    grafana_panel_ids: dict[str, int] = field(default_factory=dict)
    mig_expected: dict[str, int] = field(default_factory=dict)
    metrics_url: str = ""
    db_path: str = "/var/lib/hermes-office-gateway/gateway.db"
    action_ttl_seconds: int = 600
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080
    hermes_dashboard_url: str = ""
    hermes_dashboard_username: str = ""
    hermes_dashboard_password: str = ""

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        tokens = {role: _required(env) for role, env in _TOKEN_ENV.items()}
        extra = os.getenv("OFFICE_GRAFANA_PANELS", "")
        panels: dict[str, int] = {}
        for item in (part.strip() for part in extra.split(",") if part.strip()):
            name, sep, pid = item.partition("=")
            if not sep:
                raise ValueError("OFFICE_GRAFANA_PANELS format name=id tidak valid")
            panels[name] = int(pid)
        return cls(
            tokens=tokens,
            service_urls=_service_urls(os.getenv("OFFICE_SERVICE_URLS", "").strip()),
            write_targets={
                "lab-host": _csv_set(os.getenv("OFFICE_WRITE_LAB_HOST", "")),
                "vector": _csv_set(os.getenv("OFFICE_WRITE_VECTOR", "milvus-standalone,attu")),
            },
            vector_env=_vector_env(
                os.getenv(
                    "OFFICE_VECTOR_ENV",
                    "milvus-standalone:dev,attu:dev,milvus-prod:prod",
                )
            ),
            grafana_base_url=os.getenv("OFFICE_GRAFANA_BASE_URL", "").rstrip("/"),
            grafana_dashboards=_grafana_dashboards(os.getenv("OFFICE_GRAFANA_DASHBOARDS", "")),
            grafana_panel_ids=panels,
            mig_expected=_mig_expected(os.getenv("OFFICE_MIG_EXPECTED", "")),
            metrics_url=os.getenv("OFFICE_METRICS_URL", "").rstrip("/"),
            db_path=os.getenv(
                "OFFICE_GATEWAY_DB_PATH", "/var/lib/hermes-office-gateway/gateway.db"
            ),
            action_ttl_seconds=max(60, int(os.getenv("OFFICE_ACTION_TTL_SECONDS", "600"))),
            bind_host=os.getenv("OFFICE_GATEWAY_BIND_HOST", "0.0.0.0"),
            bind_port=int(os.getenv("OFFICE_GATEWAY_PORT", "8080")),
            hermes_dashboard_url=os.getenv("HERMES_DASHBOARD_URL", "").rstrip("/"),
            hermes_dashboard_username=os.getenv("HERMES_DASHBOARD_USERNAME", "").strip(),
            hermes_dashboard_password=os.getenv("HERMES_DASHBOARD_PASSWORD", "").strip(),
        )
