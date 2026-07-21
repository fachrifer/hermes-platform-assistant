import datetime as dt

import pytest

from core.db import Database
from core.office_monitor import (
    OfficeMonitoringService,
    SnapshotValidationError,
    deliver_due_reports,
    sign_snapshot,
)
from core.telegram_bot import TelegramInterface, get_help_text


def _snapshot(*, sequence: int, observed_at: str, status: str = "ok") -> dict:
    return {
        "observer_id": "office-observer-1",
        "sequence": sequence,
        "observed_at": observed_at,
        "services": [
            {
                "name": "openwebui",
                "status": status,
                "latency_ms": 42,
            }
        ],
        "metrics": {"gpu_utilization_pct": 55.0},
    }


def test_sign_snapshot_is_stable_and_detects_payload_changes():
    snapshot = _snapshot(sequence=1, observed_at="2026-07-20T00:00:00+00:00")

    signature = sign_snapshot(snapshot, "test-secret")

    assert signature == sign_snapshot(dict(snapshot), "test-secret")
    changed = _snapshot(sequence=2, observed_at="2026-07-20T00:00:00+00:00")
    assert signature != sign_snapshot(changed, "test-secret")


def test_ingest_rejects_sensitive_fields_and_invalid_signature(tmp_path):
    service = OfficeMonitoringService(Database(str(tmp_path / "hermes.db")))
    snapshot = _snapshot(sequence=1, observed_at="2026-07-20T00:00:00+00:00")
    snapshot["api_key"] = "must-not-be-stored"

    with pytest.raises(SnapshotValidationError, match="sensitive"):
        service.ingest(snapshot, signature="invalid", shared_secret="test-secret")


def test_ingest_is_idempotent_and_daily_report_includes_availability(tmp_path):
    service = OfficeMonitoringService(Database(str(tmp_path / "hermes.db")))
    first = _snapshot(sequence=1, observed_at="2026-07-20T00:00:00+00:00", status="ok")
    second = _snapshot(sequence=2, observed_at="2026-07-20T00:05:00+00:00", status="critical")

    assert service.ingest(first, sign_snapshot(first, "test-secret"), "test-secret") is True
    assert service.ingest(first, sign_snapshot(first, "test-secret"), "test-secret") is False
    assert service.ingest(second, sign_snapshot(second, "test-secret"), "test-secret") is True

    report = service.render_report(
        "daily",
        now=dt.datetime(2026, 7, 21, tzinfo=dt.timezone.utc),
    )

    assert "Laporan harian platform kantor" in report
    assert "Openwebui: 50.0% tersedia" in report
    assert "GPU utilization rata-rata: 55.0%" in report


def test_report_marks_missing_collection_as_coverage_gap(tmp_path):
    service = OfficeMonitoringService(Database(str(tmp_path / "hermes.db")))
    snapshot = _snapshot(sequence=1, observed_at="2026-07-20T00:00:00+00:00")
    service.ingest(snapshot, sign_snapshot(snapshot, "test-secret"), "test-secret")

    report = service.render_report(
        "daily",
        now=dt.datetime(2026, 7, 21, tzinfo=dt.timezone.utc),
    )

    assert "Cakupan monitoring: 0.3%" in report


def test_daily_report_is_generated_once_after_office_sync_window(tmp_path):
    service = OfficeMonitoringService(Database(str(tmp_path / "hermes.db")))
    snapshot = _snapshot(sequence=1, observed_at="2026-07-20T12:00:00+00:00")
    service.ingest(snapshot, sign_snapshot(snapshot, "test-secret"), "test-secret")
    now = dt.datetime(2026, 7, 21, 8, 30, tzinfo=dt.timezone.utc)

    reports = service.generate_due_reports(now)

    assert list(reports) == ["daily", "weekly", "monthly"]
    assert "Laporan harian platform kantor" in reports["daily"]
    assert service.generate_due_reports(now) == {}


@pytest.mark.asyncio
async def test_due_reports_are_delivered_without_an_llm(tmp_path):
    service = OfficeMonitoringService(Database(str(tmp_path / "hermes.db")))
    snapshot = _snapshot(sequence=1, observed_at="2026-07-20T12:00:00+00:00")
    service.ingest(snapshot, sign_snapshot(snapshot, "test-secret"), "test-secret")
    sent: list[str] = []

    async def send_report(text: str) -> None:
        sent.append(text)

    delivered = await deliver_due_reports(
        service,
        send_report,
        now=dt.datetime(2026, 7, 21, 8, 30, tzinfo=dt.timezone.utc),
    )

    assert delivered == ["daily", "weekly", "monthly"]
    assert sent and "Laporan harian platform kantor" in sent[0]


def test_report_generation_catches_up_missed_weekly_and_monthly_periods(tmp_path):
    service = OfficeMonitoringService(Database(str(tmp_path / "hermes.db")))

    reports = service.generate_due_reports(
        dt.datetime(2026, 7, 22, 8, 30, tzinfo=dt.timezone.utc)
    )

    assert {"weekly", "monthly"}.issubset(reports)


def test_cloud_retention_is_fixed_at_90_days(tmp_path):
    service = OfficeMonitoringService(Database(str(tmp_path / "hermes.db")))

    with pytest.raises(ValueError, match="90 hari"):
        service.purge_retention(days=89)


@pytest.mark.asyncio
async def test_office_command_returns_requested_report_type():
    replies: list[str] = []

    class Message:
        async def reply_text(self, text: str):
            replies.append(text)

    class Agent:
        def office_report(self, report_type: str) -> str:
            return f"report:{report_type}"

    interface = TelegramInterface.__new__(TelegramInterface)
    interface.agent = Agent()
    update = type("Update", (), {"message": Message()})()
    context = type("Context", (), {"args": ["weekly"]})()

    await interface.cmd_office(update, context)

    assert replies == ["report:weekly"]
    assert "/office [daily|weekly|monthly|status]" in get_help_text()
