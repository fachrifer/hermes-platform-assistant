"""Flush signed CML reports to Hermes Cloud with local outbox retry."""

from __future__ import annotations

import logging
from typing import Protocol

from cml.hermes_office.store import OfficeStore

logger = logging.getLogger("hermes.cml.reporter")


class ReportClient(Protocol):
    async def post_report(self, payload: dict, signature: str) -> bool: ...


class CloudReporter:
    def __init__(self, store: OfficeStore, client: ReportClient):
        self.store = store
        self.client = client

    async def flush(self, *, limit: int = 20) -> int:
        delivered = 0
        for item in self.store.dequeue_cloud_payloads(limit=limit):
            try:
                ok = await self.client.post_report(item.payload, item.signature)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to deliver office report to Hermes Cloud")
                break
            if not ok:
                break
            self.store.acknowledge_cloud_payload(item.id)
            delivered += 1
        return delivered
