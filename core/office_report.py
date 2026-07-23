"""Signed CML office reports (report + recommendation only)."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re

_ALLOWED_ROOT = {
    "observer_id",
    "generated_at",
    "period",
    "metrics",
    "selected_logs",
    "recommendation",
    "qwen_assist",
}
_PERIODS = {"status", "daily", "weekly", "monthly"}
_OBSERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_SELECTED_LOGS = 20
_MAX_LOG_LINE_CHARS = 500
_MAX_RECOMMENDATION_CHARS = 4000


class OfficeReportValidationError(ValueError):
    """Raised when a CML report payload is not safe to store."""


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _parse_timestamp(value: object) -> dt.datetime:
    if not isinstance(value, str):
        raise OfficeReportValidationError("generated_at harus ISO-8601")
    try:
        timestamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OfficeReportValidationError("generated_at tidak valid") from exc
    if timestamp.tzinfo is None:
        raise OfficeReportValidationError("generated_at harus memiliki timezone")
    return timestamp.astimezone(dt.timezone.utc)


def validate_office_report(payload: object) -> dict:
    """Validate a CML report+recommendation payload (no raw log dumps)."""
    if not isinstance(payload, dict):
        raise OfficeReportValidationError("report harus object JSON")
    if set(payload) != _ALLOWED_ROOT:
        raise OfficeReportValidationError("field report tidak diizinkan")

    observer_id = payload["observer_id"]
    if not isinstance(observer_id, str) or not _OBSERVER_ID_RE.fullmatch(observer_id):
        raise OfficeReportValidationError("observer_id tidak valid")

    generated_at = _parse_timestamp(payload["generated_at"])
    period = payload["period"]
    if period not in _PERIODS:
        raise OfficeReportValidationError("period tidak valid")

    recommendation = payload["recommendation"]
    if not isinstance(recommendation, str) or not recommendation.strip():
        raise OfficeReportValidationError("recommendation tidak valid")
    if len(recommendation) > _MAX_RECOMMENDATION_CHARS:
        raise OfficeReportValidationError("recommendation terlalu panjang")

    qwen_assist = payload["qwen_assist"]
    if not isinstance(qwen_assist, dict):
        raise OfficeReportValidationError("qwen_assist tidak valid")
    available = qwen_assist.get("available")
    model = qwen_assist.get("model")
    if not isinstance(available, bool):
        raise OfficeReportValidationError("qwen_assist.available tidak valid")
    if model is not None and (not isinstance(model, str) or len(model) > 128):
        raise OfficeReportValidationError("qwen_assist.model tidak valid")
    if set(qwen_assist) - {"available", "model"}:
        raise OfficeReportValidationError("field qwen_assist tidak diizinkan")

    metrics = payload["metrics"]
    if not isinstance(metrics, dict) or set(metrics) - {"services", "coverage"}:
        raise OfficeReportValidationError("metrics tidak valid")
    services = metrics.get("services", [])
    if not isinstance(services, list) or len(services) > 100:
        raise OfficeReportValidationError("metrics.services tidak valid")
    normalized_services: list[dict] = []
    for service in services:
        if not isinstance(service, dict):
            raise OfficeReportValidationError("metrics.services tidak valid")
        if set(service) - {"name", "healthy", "latency_ms", "status"}:
            raise OfficeReportValidationError("field service metrics tidak diizinkan")
        name = service.get("name")
        if not isinstance(name, str) or not _SERVICE_NAME_RE.fullmatch(name):
            raise OfficeReportValidationError("nama service tidak valid")
        healthy = service.get("healthy")
        status = service.get("status")
        latency = service.get("latency_ms")
        entry: dict = {"name": name}
        if healthy is not None:
            if not isinstance(healthy, bool):
                raise OfficeReportValidationError("healthy tidak valid")
            entry["healthy"] = healthy
        if status is not None:
            if status not in {"ok", "warning", "critical", "unknown"}:
                raise OfficeReportValidationError("status service tidak valid")
            entry["status"] = status
        if latency is not None:
            if (
                not isinstance(latency, (int, float))
                or isinstance(latency, bool)
                or latency < 0
                or latency > 300_000
            ):
                raise OfficeReportValidationError("latency_ms tidak valid")
            entry["latency_ms"] = float(latency)
        normalized_services.append(entry)

    coverage = metrics.get("coverage", {})
    if not isinstance(coverage, dict) or set(coverage) - {"expected_cycles", "received_cycles"}:
        raise OfficeReportValidationError("coverage tidak valid")
    normalized_coverage: dict[str, int] = {}
    for key in ("expected_cycles", "received_cycles"):
        if key not in coverage:
            continue
        value = coverage[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise OfficeReportValidationError("coverage cycles tidak valid")
        normalized_coverage[key] = value

    selected_logs = payload["selected_logs"]
    if not isinstance(selected_logs, list) or len(selected_logs) > _MAX_SELECTED_LOGS:
        raise OfficeReportValidationError("selected_logs tidak valid")
    normalized_logs: list[dict] = []
    for item in selected_logs:
        if not isinstance(item, dict) or set(item) != {"service", "ts", "line"}:
            raise OfficeReportValidationError("selected_logs item tidak valid")
        service = item["service"]
        line = item["line"]
        if not isinstance(service, str) or not _SERVICE_NAME_RE.fullmatch(service):
            raise OfficeReportValidationError("selected_logs.service tidak valid")
        if not isinstance(line, str) or not line or len(line) > _MAX_LOG_LINE_CHARS:
            raise OfficeReportValidationError("selected_logs.line tidak valid")
        ts = _parse_timestamp(item["ts"])
        normalized_logs.append({"service": service, "ts": ts.isoformat(), "line": line})

    return {
        "observer_id": observer_id,
        "generated_at": generated_at.isoformat(),
        "period": period,
        "metrics": {"services": normalized_services, "coverage": normalized_coverage},
        "selected_logs": normalized_logs,
        "recommendation": recommendation.strip(),
        "qwen_assist": {
            "available": available,
            **({"model": model} if model else {}),
        },
    }


def sign_office_report(payload: dict, shared_secret: str) -> str:
    """HMAC-SHA256 over canonical validated report payload."""
    return hmac.new(
        shared_secret.encode(),
        _canonical(validate_office_report(payload)),
        hashlib.sha256,
    ).hexdigest()


def format_office_report_content(report: dict) -> str:
    """Render Telegram/UI text from a validated office report."""
    period = report["period"]
    titles = {
        "status": "Status platform kantor (CML)",
        "daily": "Laporan harian platform kantor",
        "weekly": "Laporan mingguan platform kantor",
        "monthly": "Laporan bulanan platform kantor",
    }
    lines = [titles.get(period, "Laporan platform kantor")]
    generated = _parse_timestamp(report["generated_at"])
    lines.append(f"Dibuat: {generated.isoformat()}")
    coverage = report["metrics"].get("coverage") or {}
    if coverage:
        expected = coverage.get("expected_cycles")
        received = coverage.get("received_cycles")
        if expected and received is not None:
            pct = min(100.0, received / max(1, expected) * 100)
            lines.append(f"Cakupan monitoring: {pct:.1f}% ({received}/{expected})")
    services = report["metrics"].get("services") or []
    if services:
        lines.append("Status layanan:")
        for service in services:
            name = service["name"]
            if "healthy" in service:
                state = "ok" if service["healthy"] else "down"
            else:
                state = service.get("status", "unknown")
            latency = service.get("latency_ms")
            suffix = f" ({latency:.1f} ms)" if latency is not None else ""
            lines.append(f"- {name}: {state}{suffix}")
    assist = report.get("qwen_assist") or {}
    if not assist.get("available", False):
        lines.append("Qwen assist: unavailable")
    elif assist.get("model"):
        lines.append(f"Qwen assist: {assist['model']}")
    lines.append("Rekomendasi:")
    lines.append(report["recommendation"])
    selected = report.get("selected_logs") or []
    if selected:
        lines.append("Log terpilih:")
        for item in selected:
            lines.append(f"- [{item['service']}] {item['line']}")
    return "\n".join(lines)
