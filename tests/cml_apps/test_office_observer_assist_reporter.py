"""Tests for CML observer, Qwen assist, and cloud reporter."""

from __future__ import annotations

import datetime as dt

import pytest

from cml.hermes_office.observer import OfficeCycle
from cml.hermes_office.qwen_assist import QwenAssist
from cml.hermes_office.reporter import CloudReporter
from cml.hermes_office.store import OfficeStore
from core.office_report import validate_office_report


@pytest.mark.asyncio
async def test_qwen_assist_returns_unavailable_when_client_fails():
    class Boom:
        async def chat(self, messages):
            raise OSError("down")

    assist = QwenAssist(Boom(), model="Qwen3-8B")
    result = await assist.select_and_recommend(
        services=[{"name": "qwen3-8b", "status": "critical"}],
        logs=[{"service": "qwen3-8b", "ts": "2026-07-23T00:00:00+00:00", "line": "boom"}],
    )
    assert result.available is False
    assert result.recommendation == "qwen unavailable"
    assert result.selected_logs == []


@pytest.mark.asyncio
async def test_qwen_assist_parses_json_selection():
    class Fake:
        async def chat(self, messages):
            return (
                '{"selected_logs":[{"service":"qwen3-8b","ts":"2026-07-23T00:00:00+00:00",'
                '"line":"important"}],"recommendation":"Restart inference workers."}'
            )

    assist = QwenAssist(Fake(), model="Qwen3-8B")
    result = await assist.select_and_recommend(
        services=[{"name": "qwen3-8b", "status": "ok"}],
        logs=[{"service": "qwen3-8b", "ts": "2026-07-23T00:00:00+00:00", "line": "important"}],
    )
    assert result.available is True
    assert result.selected_logs[0]["line"] == "important"
    assert "Restart" in result.recommendation


@pytest.mark.asyncio
async def test_office_cycle_builds_signed_payload_without_raw_logs(tmp_path):
    store = OfficeStore(tmp_path / "data")

    class Collector:
        async def collect(self):
            return ([{"name": "qwen3-8b", "status": "ok", "latency_ms": 11.0}], {})

    class LogSource:
        async def collect(self):
            return [
                {
                    "service": "qwen3-8b",
                    "ts": "2026-07-23T01:00:00+00:00",
                    "line": "ready",
                }
            ]

    class Assist:
        async def select_and_recommend(self, *, services, logs):
            from cml.hermes_office.qwen_assist import AssistResult

            return AssistResult(
                available=True,
                model="Qwen3-8B",
                selected_logs=logs[:1],
                recommendation="All good.",
            )

    cycle = OfficeCycle(
        observer_id="cml-office-1",
        shared_secret="secret",
        store=store,
        collector=Collector(),
        log_source=LogSource(),
        assist=Assist(),
    )
    payload, signature = await cycle.run_once(
        now=dt.datetime(2026, 7, 23, 4, 0, tzinfo=dt.timezone.utc),
        period="status",
    )
    assert "raw_logs" not in payload
    validate_office_report(payload)
    assert payload["recommendation"] == "All good."
    assert signature
    assert store.latest_health() is not None
    assert store.list_recent_logs() 


@pytest.mark.asyncio
async def test_cloud_reporter_retries_via_outbox(tmp_path):
    store = OfficeStore(tmp_path / "data")
    store.enqueue_cloud_payload(
        {
            "observer_id": "cml-office-1",
            "generated_at": "2026-07-23T04:00:00+00:00",
            "period": "status",
            "metrics": {"services": [], "coverage": {}},
            "selected_logs": [],
            "recommendation": "ok",
            "qwen_assist": {"available": False},
        },
        "sig",
    )

    class Client:
        def __init__(self):
            self.calls = 0

        async def post_report(self, payload, signature):
            self.calls += 1
            if self.calls == 1:
                raise OSError("cloud down")
            return True

    client = Client()
    reporter = CloudReporter(store, client)
    assert await reporter.flush() == 0
    assert await reporter.flush() == 1
    assert store.dequeue_cloud_payloads() == []
