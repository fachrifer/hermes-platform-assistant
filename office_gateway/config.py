from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

_SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_TOKEN_ENV = {
    "supervisor": "OFFICE_GATEWAY_TOKEN_SUPERVISOR",
    "lab-host": "OFFICE_GATEWAY_TOKEN_LAB_HOST",
    "ingress": "OFFICE_GATEWAY_TOKEN_INGRESS",
    "llm": "OFFICE_GATEWAY_TOKEN_LLM",
    "cluster-gpu": "OFFICE_GATEWAY_TOKEN_CLUSTER",
    "vector": "OFFICE_GATEWAY_TOKEN_VECTOR",
    "obs": "OFFICE_GATEWAY_TOKEN_OBS",
    "approver": "OFFICE_GATEWAY_TOKEN_APPROVER",
}


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} wajib diisi")
    return value


_AGENT_SERVICES = (
    "hermes-agent",
    "hermes-lab-host",
    "hermes-ingress",
    "hermes-llm",
    "hermes-cluster-gpu",
    "hermes-vector",
    "hermes-obs",
)

_DEFAULT_SERVICE_URLS = ",".join(
    ["gateway=http://office-gateway:8080/health"]
    + [f"{name}=http://{name}:8642/health" for name in _AGENT_SERVICES]
)

_DEFAULT_VECTOR_INSTANCES = (
    "milvus-dev=http://host.docker.internal:9091/healthz,"
    "milvus-prod=http://10.216.203.132:9091/healthz,"
    "qdrant-dev=http://host.docker.internal:6333/readyz"
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


def _vector_instances(value: str) -> dict[str, str]:
    """Parse `name=url,...` health endpoints for vector instances."""
    return _parse_service_urls(value) if value.strip() else {}


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
    vector_instances: dict[str, str] = field(default_factory=dict)
    milvus_dev_url: str = "http://host.docker.internal:19530"
    milvus_dev_token: str = ""
    milvus_prod_url: str = ""
    milvus_prod_credentials: str = ""
    grafana_base_url: str = ""
    grafana_dashboards: dict[str, str] = field(default_factory=dict)
    grafana_panel_ids: dict[str, int] = field(default_factory=dict)
    grafana_token: str = ""
    grafana_folder_uid: str = ""
    grafana_datasource_uid: str = ""
    mig_expected: dict[str, int] = field(default_factory=dict)
    metrics_url: str = ""
    db_path: str = "/var/lib/hermes-office-gateway/gateway.db"
    action_ttl_seconds: int = 600
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080
    hermes_dashboard_url: str = ""
    hermes_dashboard_username: str = ""
    hermes_dashboard_password: str = ""
    edge_routes_path: str = ""
    edge_locations_path: str = ""
    edge_backup_dir: str = ""
    edge_tls_cert_path: str = ""
    litellm_url: str = ""
    litellm_master_key: str = ""
    console_url: str = ""

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        tokens = {role: _required(env) for role, env in _TOKEN_ENV.items()}
        if len(set(tokens.values())) != len(tokens):
            raise ValueError("OFFICE_GATEWAY_TOKEN_* values must be distinct")
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
            vector_instances=_vector_instances(
                os.getenv("OFFICE_VECTOR_INSTANCES", "").strip() or _DEFAULT_VECTOR_INSTANCES
            ),
            milvus_dev_url=(
                os.getenv("OFFICE_MILVUS_DEV_URL", "").strip() or "http://host.docker.internal:19530"
            ).rstrip("/"),
            milvus_dev_token=os.getenv("OFFICE_MILVUS_DEV_TOKEN", "").strip(),
            milvus_prod_url=os.getenv("OFFICE_MILVUS_PROD_URL", "").strip().rstrip("/"),
            milvus_prod_credentials=os.getenv("OFFICE_MILVUS_PROD_CREDENTIALS", "").strip(),
            grafana_base_url=os.getenv("OFFICE_GRAFANA_BASE_URL", "").rstrip("/"),
            grafana_dashboards=_grafana_dashboards(os.getenv("OFFICE_GRAFANA_DASHBOARDS", "")),
            grafana_panel_ids=panels,
            grafana_token=os.getenv("OFFICE_GRAFANA_TOKEN", "").strip(),
            grafana_folder_uid=os.getenv("OFFICE_GRAFANA_FOLDER_UID", "").strip(),
            grafana_datasource_uid=os.getenv("OFFICE_GRAFANA_DATASOURCE_UID", "").strip(),
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
            edge_routes_path=os.getenv("OFFICE_EDGE_ROUTES_PATH", "").strip(),
            edge_locations_path=(
                os.getenv("OFFICE_EDGE_DYNAMIC_PATH", "").strip()
                or os.getenv("OFFICE_EDGE_LOCATIONS_PATH", "").strip()
            ),
            edge_backup_dir=os.getenv("OFFICE_EDGE_BACKUP_DIR", "").strip(),
            edge_tls_cert_path=os.getenv("OFFICE_EDGE_TLS_CERT_PATH", "/certs/tls.crt").strip(),
            litellm_url=(
                os.getenv("OFFICE_LITELLM_URL", "").strip()
                or os.getenv("LITELLM_URL", "").strip()
            ),
            litellm_master_key=(
                os.getenv("OFFICE_LITELLM_MASTER_KEY", "").strip()
                or os.getenv("LITELLM_MASTER_KEY", "").strip()
            ),
            console_url=os.getenv("OFFICE_CONSOLE_URL", "").strip().rstrip("/"),
        )
