from __future__ import annotations

import asyncio
import time

import httpx


class HttpServiceCollector:
    def __init__(self, endpoints: dict[str, str]) -> None:
        self.endpoints = endpoints

    async def collect(self) -> list[dict]:
        names = list(self.endpoints)
        if not names:
            return []
        return list(await asyncio.gather(*(self.collect_one(name) for name in names)))

    async def collect_one(self, name: str) -> dict:
        url = self.endpoints.get(name)
        if not url:
            return {"name": name, "status": "unknown"}

        try:
            start = time.monotonic()
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
            latency_ms = int((time.monotonic() - start) * 1000)
            status = "ok" if response.status_code < 400 else "critical"
            return {"name": name, "status": status, "latency_ms": latency_ms}
        except Exception:
            return {"name": name, "status": "unknown"}
