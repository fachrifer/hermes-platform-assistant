"""Tests for CML office report ingest on Hermes Cloud."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from core import main
from core.db import Database
from core.office_monitor import OfficeMonitoringService
from core.office_report import format_office_report_content, sign_office_report, validate_office_report


def _report(**overrides) -> dict:
    payload = {
        "observer_id": "cml-office-1",
        "generated_at": "2026-07-22T04:00:00+00:00",
        "period": "daily",
        "metrics": {
            "services": [{"name": "qwen3-8b", "healthy": True, "latency_ms": 12.0}],
            "coverage": {"expected_cycles": 288, "received_cycles": 287},
        },
        "selected_logs": [
            {
                "service": "qwen3-8b",
                "ts": "2026-07-22T03:11:00+00:00",
                "line": "CUDA OOM avoided",
            }
        ],
        "recommendation": "Keep batch size at 1 on V100.",
        "qwen_assist": {"available": True, "model": "Qwen3-8B"},
    }
    payload.update(overrides)
    return payload


def test_sign_office_report_is_stable():
    report = _report()
    assert sign_office_report(report, "secret") == sign_office_report(dict(report), "secret")


def test_validate_office_report_rejects_raw_logs_field():
    report = _report()
    report["raw_logs"] = ["must not appear"]
    try:
        validate_office_report(report)
        assert False, "expected validation error"
    except ValueError as exc:
        assert "diizinkan" in str(exc).casefold() or "field" in str(exc).casefold()


def test_format_office_report_includes_recommendation_and_selected_logs():
    content = format_office_report_content(validate_office_report(_report()))
    assert "Rekomendasi:" in content
    assert "Keep batch size at 1 on V100." in content
    assert "CUDA OOM avoided" in content
    assert "qwen3-8b" in content.casefold() or "Qwen3" in content


def test_ingest_report_persists_and_render_prefers_stored_content(tmp_path):
    service = OfficeMonitoringService(Database(str(tmp_path / "hermes.db")))
    report = _report()
    assert service.ingest_report(report, sign_office_report(report, "secret"), "secret") is True
    rendered = service.render_report(
        "daily",
        now=dt.datetime(2026, 7, 23, tzinfo=dt.timezone.utc),
    )
    assert "Keep batch size at 1 on V100." in rendered


def test_cml_report_api_accepts_hmac_without_mtls_when_subject_unset(tmp_path, monkeypatch):
    report = _report()
    main.app.state.office_monitoring = OfficeMonitoringService(Database(str(tmp_path / "hermes.db")))
    monkeypatch.setattr(main.settings, "office_observer_id", "cml-office-1", raising=False)
    monkeypatch.setattr(main.settings, "office_observer_shared_secret", "secret", raising=False)
    monkeypatch.setattr(main.settings, "office_observer_mtls_subject", "", raising=False)
    client = TestClient(main.app)

    denied = client.post("/api/v1/office/reports", json=report)
    assert denied.status_code == 401

    ok = client.post(
        "/api/v1/office/reports",
        json=report,
        headers={
            "X-Hermes-Observer": "cml-office-1",
            "X-Hermes-Signature": sign_office_report(report, "secret"),
        },
    )
    assert ok.status_code == 202
    assert ok.json()["accepted"] is True


def test_cml_report_api_rejects_bad_signature(tmp_path, monkeypatch):
    report = _report()
    main.app.state.office_monitoring = OfficeMonitoringService(Database(str(tmp_path / "hermes.db")))
    monkeypatch.setattr(main.settings, "office_observer_id", "cml-office-1", raising=False)
    monkeypatch.setattr(main.settings, "office_observer_shared_secret", "secret", raising=False)
    monkeypatch.setattr(main.settings, "office_observer_mtls_subject", "", raising=False)
    client = TestClient(main.app)

    bad = client.post(
        "/api/v1/office/reports",
        json=report,
        headers={
            "X-Hermes-Observer": "cml-office-1",
            "X-Hermes-Signature": "deadbeef",
        },
    )
    assert bad.status_code == 401
