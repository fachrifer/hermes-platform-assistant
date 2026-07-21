"""Signed office-monitoring snapshots and deterministic reports."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
from collections import defaultdict
from statistics import fmean
from typing import Awaitable, Callable

from core.db import Database

_ALLOWED_ROOT_FIELDS = {"observer_id", "sequence", "observed_at", "services", "metrics"}
_ALLOWED_SERVICE_FIELDS = {"name", "status", "latency_ms"}
_ALLOWED_METRICS = {
    "gpu_utilization_pct",
    "gpu_memory_used_pct",
    "error_rate_pct",
    "request_rate",
    "storage_used_pct",
}
_SENSITIVE_FIELD_PARTS = (
    "secret",
    "password",
    "token",
    "api_key",
    "authorization",
    "prompt",
    "chat",
    "content",
    "document",
    "file",
    "url",
    "query",
    "command",
)
_SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_OBSERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_STATUSES = {"ok", "warning", "critical", "unknown"}
MONITORING_RETENTION_DAYS = 90


class SnapshotValidationError(ValueError):
    """Raised when an observer payload is not safe to store."""


def _canonical_snapshot(snapshot: dict) -> bytes:
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sign_snapshot(snapshot: dict, shared_secret: str) -> str:
    """Return the end-to-end signature verified by Hermes Cloud."""
    return hmac.new(
        shared_secret.encode(), _canonical_snapshot(validate_snapshot(snapshot)), hashlib.sha256
    ).hexdigest()


def _parse_timestamp(value: object) -> dt.datetime:
    if not isinstance(value, str):
        raise SnapshotValidationError("observed_at harus ISO-8601")
    try:
        timestamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotValidationError("observed_at tidak valid") from exc
    if timestamp.tzinfo is None:
        raise SnapshotValidationError("observed_at harus memiliki timezone")
    return timestamp.astimezone(dt.timezone.utc)


def validate_snapshot(snapshot: object) -> dict:
    """Validate a compact operational snapshot before persisting it."""
    if not isinstance(snapshot, dict):
        raise SnapshotValidationError("snapshot harus object JSON")
    sensitive = [
        key
        for key in snapshot
        if any(part in str(key).casefold() for part in _SENSITIVE_FIELD_PARTS)
    ]
    if sensitive:
        raise SnapshotValidationError("snapshot memuat field sensitive")
    if set(snapshot) != _ALLOWED_ROOT_FIELDS:
        raise SnapshotValidationError("field snapshot tidak diizinkan")

    observer_id = snapshot["observer_id"]
    if not isinstance(observer_id, str) or not _OBSERVER_ID_RE.fullmatch(observer_id):
        raise SnapshotValidationError("observer_id tidak valid")
    sequence = snapshot["sequence"]
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise SnapshotValidationError("sequence tidak valid")
    observed_at = _parse_timestamp(snapshot["observed_at"])

    services = snapshot["services"]
    if not isinstance(services, list) or not services or len(services) > 100:
        raise SnapshotValidationError("services tidak valid")
    normalized_services: list[dict] = []
    for service in services:
        if not isinstance(service, dict) or set(service) - _ALLOWED_SERVICE_FIELDS:
            raise SnapshotValidationError("field service tidak diizinkan")
        name = service.get("name")
        status = service.get("status")
        latency = service.get("latency_ms")
        if not isinstance(name, str) or not _SERVICE_NAME_RE.fullmatch(name):
            raise SnapshotValidationError("nama service tidak valid")
        if status not in _STATUSES:
            raise SnapshotValidationError("status service tidak valid")
        if latency is not None and (
            not isinstance(latency, (int, float))
            or isinstance(latency, bool)
            or latency < 0
            or latency > 300_000
        ):
            raise SnapshotValidationError("latency_ms tidak valid")
        normalized_services.append(
            {"name": name, "status": status, **({"latency_ms": float(latency)} if latency is not None else {})}
        )

    metrics = snapshot["metrics"]
    if not isinstance(metrics, dict) or set(metrics) - _ALLOWED_METRICS:
        raise SnapshotValidationError("metric tidak diizinkan")
    normalized_metrics: dict[str, float] = {}
    for key, value in metrics.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise SnapshotValidationError("nilai metric harus angka")
        if key.endswith("_pct") and not 0 <= value <= 100:
            raise SnapshotValidationError("metric persentase tidak valid")
        normalized_metrics[key] = float(value)

    return {
        "observer_id": observer_id,
        "sequence": sequence,
        "observed_at": observed_at.isoformat(),
        "services": normalized_services,
        "metrics": normalized_metrics,
    }


class OfficeMonitoringService:
    """Stores verified snapshots and renders non-LLM operational reports."""

    def __init__(self, db: Database):
        self.db = db

    def ingest(self, snapshot: object, signature: str, shared_secret: str) -> bool:
        normalized = validate_snapshot(snapshot)
        if not shared_secret or not isinstance(signature, str):
            raise SnapshotValidationError("autentikasi observer tidak tersedia")
        expected = sign_snapshot(normalized, shared_secret)
        # Signatures are calculated over canonical normalized data so equivalent JSON is stable.
        if not hmac.compare_digest(expected, signature):
            raise SnapshotValidationError("signature observer tidak valid")
        return self.db.save_monitoring_snapshot(
            normalized["observer_id"],
            normalized["sequence"],
            normalized["observed_at"],
            _canonical_snapshot(normalized).decode(),
        )

    def render_report(self, report_type: str, *, now: dt.datetime | None = None) -> str:
        now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        start, end, title = self._period(report_type, now)
        rows = self.db.list_monitoring_snapshots(start.isoformat(), end.isoformat())
        snapshots = [json.loads(row["payload_json"]) for row in rows]
        expected_snapshots = max(1, int((end - start).total_seconds() // 300))
        coverage = min(100.0, len(snapshots) / expected_snapshots * 100)
        service_states: dict[str, list[str]] = defaultdict(list)
        gpu_values: list[float] = []
        for snapshot in snapshots:
            for service in snapshot["services"]:
                service_states[service["name"]].append(service["status"])
            gpu = snapshot["metrics"].get("gpu_utilization_pct")
            if gpu is not None:
                gpu_values.append(float(gpu))

        lines = [
            title,
            f"Periode: {start.date().isoformat()} s.d. {(end - dt.timedelta(microseconds=1)).date().isoformat()}",
            f"Cakupan monitoring: {coverage:.1f}%",
        ]
        if not snapshots:
            lines.append("Tidak ada snapshot tersinkronisasi untuk periode ini.")
            return "\n".join(lines)
        lines.append("Ketersediaan layanan:")
        for name in sorted(service_states):
            states = service_states[name]
            available = sum(state in {"ok", "warning"} for state in states) / len(states) * 100
            lines.append(f"- {name.capitalize()}: {available:.1f}% tersedia")
        if gpu_values:
            lines.append(f"GPU utilization rata-rata: {fmean(gpu_values):.1f}%")
        return "\n".join(lines)

    def generate_due_reports(self, now: dt.datetime | None = None) -> dict[str, str]:
        """Persist each scheduled report once after the office-hour sync window."""
        now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        # Re-attempt the latest completed weekly/monthly period on every run.
        # This recovers reports missed while Hermes Cloud was restarting.
        due = ["daily", "weekly", "monthly"] if now.time() >= dt.time(8, 0) else []

        generated: dict[str, str] = {}
        for report_type in due:
            start, end, _ = self._period(report_type, now)
            content = self.render_report(report_type, now=now)
            if self.db.save_monitoring_report(
                report_type, start.isoformat(), end.isoformat(), content
            ):
                generated[report_type] = content
        return generated

    def purge_retention(
        self, *, now: dt.datetime | None = None, days: int = MONITORING_RETENTION_DAYS
    ) -> None:
        if days != MONITORING_RETENTION_DAYS:
            raise ValueError("retention monitoring harus tepat 90 hari")
        now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        self.db.purge_monitoring_before((now - dt.timedelta(days=days)).isoformat())

    @staticmethod
    def _period(report_type: str, now: dt.datetime) -> tuple[dt.datetime, dt.datetime, str]:
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if report_type == "daily":
            return today - dt.timedelta(days=1), today, "Laporan harian platform kantor"
        if report_type == "weekly":
            end = today - dt.timedelta(days=today.weekday())
            return end - dt.timedelta(days=7), end, "Laporan mingguan platform kantor"
        if report_type == "monthly":
            end = today.replace(day=1)
            start = (end - dt.timedelta(days=1)).replace(day=1)
            return start, end, "Laporan bulanan platform kantor"
        raise ValueError("report_type harus daily, weekly, atau monthly")


async def deliver_due_reports(
    service: OfficeMonitoringService,
    send_report: Callable[[str], Awaitable[None]],
    *,
    now: dt.datetime | None = None,
) -> list[str]:
    """Generate and deliver scheduled reports without using an LLM."""
    reports = service.generate_due_reports(now)
    for content in reports.values():
        await send_report(content)
    return list(reports)
