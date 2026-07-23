"""Tests for CML hermes-office Manager UI."""

from __future__ import annotations

from fastapi.testclient import TestClient

from cml.hermes_office.app import create_app
from cml.hermes_office.store import OfficeStore


def test_ui_status_and_reports_endpoints(tmp_path):
    store = OfficeStore(tmp_path / "data")
    store.save_health(
        "2026-07-23T04:00:00+00:00",
        [{"name": "qwen3-8b", "status": "ok", "latency_ms": 10}],
        {},
    )
    store.save_report("status", "Status platform\nRekomendasi:\nAll good.")
    store.append_logs("qwen3-8b", [{"ts": "2026-07-23T04:00:00+00:00", "line": "hello"}])

    client = TestClient(create_app(store))
    status = client.get("/api/status")
    assert status.status_code == 200
    assert status.json()["health"]["services"][0]["name"] == "qwen3-8b"

    reports = client.get("/api/reports")
    assert reports.status_code == 200
    assert reports.json()[0]["period"] == "status"

    logs = client.get("/api/logs")
    assert logs.status_code == 200
    assert logs.json()[0]["line"] == "hello"

    home = client.get("/")
    assert home.status_code == 200
    assert "Hermes Office" in home.text
