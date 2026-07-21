"""Collection and store-and-forward loop for the office observer."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Protocol

import httpx
from core.office_monitor import sign_snapshot
from office_observer.spool import SnapshotSpool

logger = logging.getLogger("hermes.office_observer")


class Collector(Protocol):
    async def collect(self) -> tuple[list[dict], dict[str, float]]: ...


class RelayClient(Protocol):
    async def send(self, snapshot: dict, signature: str) -> bool: ...


class OfficeObserver:
    """Collect fixed health data locally and forward only after relay acknowledgement."""

    def __init__(
        self,
        *,
        observer_id: str,
        shared_secret: str,
        collector: Collector,
        spool: SnapshotSpool,
        relay: RelayClient,
        backlog_retention_days: int = 14,
    ):
        self.observer_id = observer_id
        self.shared_secret = shared_secret
        self.collector = collector
        self.spool = spool
        self.relay = relay
        self.backlog_retention_days = backlog_retention_days

    async def collect_once(self) -> dict:
        services, metrics = await self.collector.collect()
        snapshot = {
            "observer_id": self.observer_id,
            "sequence": self.spool.next_sequence(),
            "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "services": services,
            "metrics": metrics,
        }
        signature = sign_snapshot(snapshot, self.shared_secret)
        self.spool.enqueue(snapshot, signature)
        return snapshot

    async def sync_once(self) -> int:
        sent = 0
        for item in self.spool.pending():
            try:
                delivered = await self.relay.send(item.snapshot, item.signature)
            except (httpx.HTTPError, OSError, asyncio.TimeoutError) as exc:
                logger.info("Laptop relay belum tersedia: %s", exc)
                break
            if not delivered:
                break
            self.spool.acknowledge(item.snapshot["sequence"])
            sent += 1
        return sent

    def purge_backlog(self, *, now: dt.datetime | None = None) -> None:
        now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        cutoff = now - dt.timedelta(days=self.backlog_retention_days)
        self.spool.purge_before(cutoff.isoformat())

    async def run_cycle(self, *, now: dt.datetime | None = None) -> int:
        """Collect, synchronize, and always enforce the local backlog boundary."""
        try:
            await self.collect_once()
            return await self.sync_once()
        finally:
            self.purge_backlog(now=now)

    async def run(self, interval_seconds: int = 300) -> None:
        while True:
            try:
                await self.run_cycle()
            except Exception:  # noqa: BLE001
                logger.exception("Siklus office observer gagal")
            await asyncio.sleep(interval_seconds)
