"""Environment configuration for the office gateway."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

_SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

ALLOWLISTED_ACTIONS = frozenset(
    {
        "restart_service",
        "scale_replicas",
        "clear_queue",
        "set_feature_flag",
    }
)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} harus angka") from exc


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} wajib diisi")
    return value


def _service_urls(value: str) -> dict[str, str]:
    endpoints: dict[str, str] = {}
    for item in (part.strip() for part in value.split(",") if part.strip()):
        name, separator, url = item.partition("=")
        if not separator or not _SERVICE_NAME_RE.fullmatch(name) or not url.startswith(
            ("http://", "https://")
        ):
            raise ValueError("OFFICE_SERVICE_URLS berisi service atau URL tidak valid")
        endpoints[name] = url
    if not endpoints:
        raise ValueError("OFFICE_SERVICE_URLS wajib memiliki minimal satu service")
    return endpoints


def _write_targets(value: str) -> dict[str, frozenset[str]]:
    """Parse `name:action|action,...` into service → allowed actions."""
    if not value.strip():
        return {}
    targets: dict[str, frozenset[str]] = {}
    for item in (part.strip() for part in value.split(",") if part.strip()):
        name, separator, actions_raw = item.partition(":")
        if not separator or not _SERVICE_NAME_RE.fullmatch(name):
            raise ValueError("OFFICE_WRITE_TARGETS berisi target tidak valid")
        actions = frozenset(a.strip() for a in actions_raw.split("|") if a.strip())
        unknown = actions - ALLOWLISTED_ACTIONS
        if not actions or unknown:
            raise ValueError(
                f"OFFICE_WRITE_TARGETS actions tidak valid untuk {name}: {sorted(unknown or actions)}"
            )
        targets[name] = actions
    return targets


def _scale_bounds(value: str) -> dict[str, tuple[int, int]]:
    """Parse `name:min-max,...`."""
    if not value.strip():
        return {}
    bounds: dict[str, tuple[int, int]] = {}
    for item in (part.strip() for part in value.split(",") if part.strip()):
        name, separator, range_raw = item.partition(":")
        if not separator or not _SERVICE_NAME_RE.fullmatch(name):
            raise ValueError("OFFICE_SCALE_BOUNDS berisi target tidak valid")
        low_s, dash, high_s = range_raw.partition("-")
        if not dash:
            raise ValueError(f"OFFICE_SCALE_BOUNDS format min-max tidak valid untuk {name}")
        try:
            low, high = int(low_s), int(high_s)
        except ValueError as exc:
            raise ValueError(f"OFFICE_SCALE_BOUNDS angka tidak valid untuk {name}") from exc
        if low < 0 or high < low:
            raise ValueError(f"OFFICE_SCALE_BOUNDS rentang tidak valid untuk {name}")
        bounds[name] = (low, high)
    return bounds


def _adapter_endpoints(value: str) -> dict[tuple[str, str], str]:
    """Parse `name.action=url,...` into (service, action) → URL."""
    if not value.strip():
        return {}
    endpoints: dict[tuple[str, str], str] = {}
    for item in (part.strip() for part in value.split(",") if part.strip()):
        key, separator, url = item.partition("=")
        if not separator or not url.startswith(("http://", "https://")):
            raise ValueError("OFFICE_ADAPTER_ENDPOINTS berisi endpoint tidak valid")
        name, dot, action = key.partition(".")
        if not dot or not _SERVICE_NAME_RE.fullmatch(name) or action not in ALLOWLISTED_ACTIONS:
            raise ValueError(f"OFFICE_ADAPTER_ENDPOINTS key tidak valid: {key}")
        endpoints[(name, action)] = url
    return endpoints


@dataclass(frozen=True)
class GatewayConfig:
    token: str
    service_urls: dict[str, str]
    write_targets: dict[str, frozenset[str]] = field(default_factory=dict)
    scale_bounds: dict[str, tuple[int, int]] = field(default_factory=dict)
    adapter_endpoints: dict[tuple[str, str], str] = field(default_factory=dict)
    db_path: str = "/var/lib/hermes-office-gateway/gateway.db"
    action_ttl_seconds: int = 600
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080
    host_enabled: bool = False
    docker_sock: str = "/var/run/docker.sock"
    host_proc: str = "/host/proc"
    host_root: str = "/host/root"
    host_cpu_critical: float = 90.0
    host_memory_critical: float = 90.0
    host_disk_critical: float = 90.0

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        return cls(
            token=_required("OFFICE_GATEWAY_TOKEN"),
            service_urls=_service_urls(_required("OFFICE_SERVICE_URLS")),
            write_targets=_write_targets(os.getenv("OFFICE_WRITE_TARGETS", "")),
            scale_bounds=_scale_bounds(os.getenv("OFFICE_SCALE_BOUNDS", "")),
            adapter_endpoints=_adapter_endpoints(os.getenv("OFFICE_ADAPTER_ENDPOINTS", "")),
            db_path=os.getenv(
                "OFFICE_GATEWAY_DB_PATH", "/var/lib/hermes-office-gateway/gateway.db"
            ),
            action_ttl_seconds=max(60, int(os.getenv("OFFICE_ACTION_TTL_SECONDS", "600"))),
            bind_host=os.getenv("OFFICE_GATEWAY_BIND_HOST", "0.0.0.0"),
            bind_port=int(os.getenv("OFFICE_GATEWAY_PORT", "8080")),
            host_enabled=_truthy(os.getenv("OFFICE_HOST_ENABLED", "0")),
            docker_sock=os.getenv("OFFICE_DOCKER_SOCK", "/var/run/docker.sock"),
            host_proc=os.getenv("OFFICE_HOST_PROC", "/host/proc"),
            host_root=os.getenv("OFFICE_HOST_ROOT", "/host/root"),
            host_cpu_critical=_float_env("OFFICE_HOST_CPU_CRITICAL", 90.0),
            host_memory_critical=_float_env("OFFICE_HOST_MEMORY_CRITICAL", 90.0),
            host_disk_critical=_float_env("OFFICE_HOST_DISK_CRITICAL", 90.0),
        )

    def allows(self, target: str, action: str) -> bool:
        return action in self.write_targets.get(target, frozenset())
