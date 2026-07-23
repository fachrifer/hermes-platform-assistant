"""Tests for CML hermes-office local store."""

from __future__ import annotations

import datetime as dt

from cml.hermes_office.store import OfficeStore


def test_append_and_list_recent_logs_respects_cap(tmp_path):
    store = OfficeStore(tmp_path / "data")
    store.append_logs(
        "qwen3-8b",
        [
            {"ts": "2026-07-23T01:00:00+00:00", "line": f"line-{i}"}
            for i in range(5)
        ],
    )
    recent = store.list_recent_logs("qwen3-8b", limit=3)
    assert len(recent) == 3
    assert recent[-1]["line"] == "line-4"


def test_purge_logs_removes_old_entries(tmp_path):
    store = OfficeStore(tmp_path / "data")
    store.append_logs(
        "qwen3-8b",
        [
            {"ts": "2026-06-01T00:00:00+00:00", "line": "old"},
            {"ts": "2026-07-23T00:00:00+00:00", "line": "new"},
        ],
    )
    store.purge_logs(now=dt.datetime(2026, 7, 23, tzinfo=dt.timezone.utc), days=14)
    lines = [item["line"] for item in store.list_recent_logs("qwen3-8b", limit=10)]
    assert lines == ["new"]


def test_cloud_outbox_queue_fifo(tmp_path):
    store = OfficeStore(tmp_path / "data")
    store.enqueue_cloud_payload({"period": "status", "n": 1}, "sig-1")
    store.enqueue_cloud_payload({"period": "daily", "n": 2}, "sig-2")
    pending = store.dequeue_cloud_payloads(limit=10)
    assert [item.payload["n"] for item in pending] == [1, 2]
    store.acknowledge_cloud_payload(pending[0].id)
    assert [item.payload["n"] for item in store.dequeue_cloud_payloads()] == [2]


def test_save_and_list_reports(tmp_path):
    store = OfficeStore(tmp_path / "data")
    store.save_report("daily", "content-a")
    store.save_report("status", "content-b")
    reports = store.list_reports(limit=5)
    assert reports[0]["period"] == "status"
    assert store.latest_report("daily")["content"] == "content-a"
