"""Environment config for the CML hermes-office application."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _parse_mapping(raw: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise RuntimeError(f"Invalid mapping entry: {part}")
        key, value = part.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key or not value:
            raise RuntimeError(f"Invalid mapping entry: {part}")
        mapping[key] = value
    return mapping


@dataclass(frozen=True)
class OfficeConfig:
    observer_id: str
    shared_secret: str
    cloud_report_url: str
    qwen_base_url: str
    data_dir: str
    service_urls: dict[str, str]
    log_paths: dict[str, str]
    interval_seconds: int
    qwen_model: str

    @classmethod
    def from_env(cls) -> "OfficeConfig":
        return cls(
            observer_id=_required("OFFICE_OBSERVER_ID"),
            shared_secret=_required("OFFICE_OBSERVER_SHARED_SECRET"),
            cloud_report_url=_required("HERMES_CLOUD_REPORT_URL"),
            qwen_base_url=_required("QWEN_BASE_URL").rstrip("/"),
            data_dir=os.getenv("OFFICE_DATA_DIR", "./data/hermes_office").strip()
            or "./data/hermes_office",
            service_urls=_parse_mapping(_required("OFFICE_SERVICE_URLS")),
            log_paths=_parse_mapping(os.getenv("OFFICE_LOG_SOURCES", "")),
            interval_seconds=int(os.getenv("OFFICE_INTERVAL_SECONDS", "300")),
            qwen_model=os.getenv("QWEN_MODEL", "Qwen3-8B").strip() or "Qwen3-8B",
        )
