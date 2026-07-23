"""One monitoring cycle: health + logs → assist → local store → cloud outbox."""

from __future__ import annotations

import datetime as dt
from typing import Protocol

from core.office_report import sign_office_report, validate_office_report
from cml.hermes_office.qwen_assist import AssistResult
from cml.hermes_office.store import OfficeStore


class Collector(Protocol):
    async def collect(self) -> tuple[list[dict], dict]: ...


class LogSource(Protocol):
    async def collect(self) -> list[dict]: ...


class Assist(Protocol):
    async def select_and_recommend(self, *, services: list[dict], logs: list[dict]) -> AssistResult: ...


class OfficeCycle:
    def __init__(
        self,
        *,
        observer_id: str,
        shared_secret: str,
        store: OfficeStore,
        collector: Collector,
        log_source: LogSource,
        assist: Assist,
    ):
        self.observer_id = observer_id
        self.shared_secret = shared_secret
        self.store = store
        self.collector = collector
        self.log_source = log_source
        self.assist = assist
        self._cycles = 0

    async def run_once(
        self,
        *,
        now: dt.datetime | None = None,
        period: str = "status",
        expected_cycles: int | None = None,
    ) -> tuple[dict, str]:
        now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        services, metrics = await self.collector.collect()
        log_entries = await self.log_source.collect()
        for entry in log_entries:
            self.store.append_logs(entry["service"], [{"ts": entry["ts"], "line": entry["line"]}])
        self.store.save_health(now.isoformat(), services, metrics)
        self._cycles += 1
        self.store.purge_logs(now=now, days=14)

        assist = await self.assist.select_and_recommend(services=services, logs=log_entries)
        metric_services = []
        for service in services:
            metric_services.append(
                {
                    "name": service["name"],
                    "healthy": service.get("status") in {"ok", "warning"},
                    "status": service.get("status", "unknown"),
                    **(
                        {"latency_ms": float(service["latency_ms"])}
                        if service.get("latency_ms") is not None
                        else {}
                    ),
                }
            )
        payload = {
            "observer_id": self.observer_id,
            "generated_at": now.isoformat(),
            "period": period,
            "metrics": {
                "services": metric_services,
                "coverage": {
                    "expected_cycles": expected_cycles or max(1, self._cycles),
                    "received_cycles": self._cycles,
                },
            },
            "selected_logs": assist.selected_logs,
            "recommendation": assist.recommendation,
            "qwen_assist": {
                "available": assist.available,
                "model": assist.model,
            },
        }
        normalized = validate_office_report(payload)
        signature = sign_office_report(normalized, self.shared_secret)
        from core.office_report import format_office_report_content

        self.store.save_report(period, format_office_report_content(normalized), created_at=now.isoformat())
        self.store.enqueue_cloud_payload(normalized, signature)
        return normalized, signature
