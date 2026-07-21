"""Environment configuration for the always-on office observer."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

_SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} wajib diisi")
    return value


def _service_urls(value: str) -> dict[str, str]:
    endpoints: dict[str, str] = {}
    for item in (part.strip() for part in value.split(",") if part.strip()):
        name, separator, url = item.partition("=")
        if not separator or not _SERVICE_NAME_RE.fullmatch(name) or not url.startswith(("http://", "https://")):
            raise ValueError("OFFICE_SERVICE_URLS berisi service atau URL tidak valid")
        endpoints[name] = url
    if not endpoints:
        raise ValueError("OFFICE_SERVICE_URLS wajib memiliki minimal satu service")
    return endpoints


@dataclass(frozen=True)
class ObserverConfig:
    observer_id: str
    shared_secret: str
    relay_url: str
    spool_path: str
    service_urls: dict[str, str]
    interval_seconds: int
    relay_ca_file: str
    relay_client_cert: str
    relay_client_key: str

    @classmethod
    def from_env(cls) -> "ObserverConfig":
        relay_url = _required("OFFICE_RELAY_URL").rstrip("/")
        if not relay_url.startswith("https://"):
            raise ValueError("OFFICE_RELAY_URL harus HTTPS untuk mTLS")
        return cls(
            observer_id=_required("OFFICE_OBSERVER_ID"),
            shared_secret=_required("OFFICE_OBSERVER_SHARED_SECRET"),
            relay_url=relay_url,
            spool_path=os.getenv("OFFICE_OBSERVER_SPOOL_PATH", "/var/lib/hermes-observer/observer.db"),
            service_urls=_service_urls(_required("OFFICE_SERVICE_URLS")),
            interval_seconds=max(60, int(os.getenv("OFFICE_OBSERVER_INTERVAL_SECONDS", "300"))),
            relay_ca_file=_required("OFFICE_RELAY_CA_FILE"),
            relay_client_cert=_required("OFFICE_RELAY_CLIENT_CERT"),
            relay_client_key=_required("OFFICE_RELAY_CLIENT_KEY"),
        )
