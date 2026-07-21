import datetime as dt

import httpx
import pytest

from office_observer.main import OfficeObserver
from office_observer.collectors import HttpServiceCollector
from office_observer.relay_client import relay_response_acknowledged
from office_observer.spool import SnapshotSpool


def test_spool_orders_snapshots_and_removes_acknowledged_records(tmp_path):
    spool = SnapshotSpool(str(tmp_path / "observer.db"))
    first = {"sequence": 1, "observer_id": "office-observer-1"}
    second = {"sequence": 2, "observer_id": "office-observer-1"}

    spool.enqueue(first, "signature-1")
    spool.enqueue(second, "signature-2")

    assert [item.snapshot["sequence"] for item in spool.pending()] == [1, 2]
    spool.acknowledge(1)
    assert [item.snapshot["sequence"] for item in spool.pending()] == [2]


@pytest.mark.asyncio
async def test_observer_retries_signed_backlog_after_laptop_relay_recovers(tmp_path):
    class Collector:
        async def collect(self):
            return ([{"name": "openwebui", "status": "ok", "latency_ms": 10}], {})

    class Relay:
        def __init__(self):
            self.available = False
            self.received = []

        async def send(self, snapshot, signature):
            if not self.available:
                raise OSError("laptop relay offline")
            self.received.append((snapshot, signature))
            return True

    spool = SnapshotSpool(str(tmp_path / "observer.db"))
    relay = Relay()
    observer = OfficeObserver(
        observer_id="office-observer-1",
        shared_secret="test-secret",
        collector=Collector(),
        spool=spool,
        relay=relay,
    )

    await observer.collect_once()
    assert await observer.sync_once() == 0

    relay.available = True
    assert await observer.sync_once() == 1
    assert relay.received[0][0]["observer_id"] == "office-observer-1"
    assert spool.pending() == []


@pytest.mark.asyncio
async def test_http_collector_exports_operational_status_without_internal_urls():
    async def request(url: str) -> tuple[int, float]:
        assert url.startswith("https://office.example/")
        return 200, 13.5

    collector = HttpServiceCollector(
        {"openwebui": "https://office.example/openwebui/health"}, request=request
    )

    services, metrics = await collector.collect()

    assert services == [{"name": "openwebui", "status": "ok", "latency_ms": 13.5}]
    assert metrics == {}
    assert "office.example" not in str(services)


def test_duplicate_cloud_snapshot_is_an_acknowledged_delivery():
    assert relay_response_acknowledged(202, {"accepted": False})
    assert not relay_response_acknowledged(401, {"accepted": False})


def test_observer_purges_unsynchronized_records_older_than_14_days():
    class Spool:
        def __init__(self):
            self.cutoff = None

        def purge_before(self, cutoff: str) -> None:
            self.cutoff = cutoff

    spool = Spool()
    observer = OfficeObserver(
        observer_id="office-observer-1",
        shared_secret="test-secret",
        collector=None,
        spool=spool,
        relay=None,
    )

    observer.purge_backlog(now=dt.datetime(2026, 7, 21, tzinfo=dt.timezone.utc))

    assert spool.cutoff == "2026-07-07T00:00:00+00:00"


@pytest.mark.asyncio
async def test_observer_purges_backlog_when_relay_http_connection_fails():
    class Collector:
        async def collect(self):
            return ([{"name": "openwebui", "status": "ok"}], {})

    class Spool:
        def __init__(self):
            self.cutoff = None

        def next_sequence(self):
            return 1

        def enqueue(self, snapshot, signature):
            return None

        def pending(self):
            return [type("Stored", (), {"snapshot": {"sequence": 1}, "signature": "sig"})()]

        def acknowledge(self, sequence):
            raise AssertionError("unavailable relay must not acknowledge")

        def purge_before(self, cutoff):
            self.cutoff = cutoff

    class Relay:
        async def send(self, snapshot, signature):
            raise httpx.ConnectError("relay unavailable")

    spool = Spool()
    observer = OfficeObserver(
        observer_id="office-observer-1",
        shared_secret="test-secret",
        collector=Collector(),
        spool=spool,
        relay=Relay(),
    )

    await observer.run_cycle(now=dt.datetime(2026, 7, 21, tzinfo=dt.timezone.utc))

    assert spool.cutoff == "2026-07-07T00:00:00+00:00"
