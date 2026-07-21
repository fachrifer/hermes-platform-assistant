"""Fixed, read-only health collectors for intranet AI services."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import httpx

Request = Callable[[str], Awaitable[tuple[int, float]]]


class HttpServiceCollector:
    """Collect configured health URLs without ever exporting their addresses."""

    def __init__(self, endpoints: dict[str, str], *, request: Request | None = None):
        self.endpoints = endpoints
        self._request = request or self._http_request

    async def collect(self) -> tuple[list[dict], dict[str, float]]:
        checks = await asyncio.gather(
            *(self._check(name, url) for name, url in self.endpoints.items())
        )
        return list(checks), {}

    async def _check(self, name: str, url: str) -> dict:
        try:
            status_code, latency_ms = await self._request(url)
        except (httpx.HTTPError, OSError, asyncio.TimeoutError):
            return {"name": name, "status": "unknown"}
        return {
            "name": name,
            "status": "ok" if 200 <= status_code < 400 else "critical",
            "latency_ms": round(latency_ms, 1),
        }

    @staticmethod
    async def _http_request(url: str) -> tuple[int, float]:
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(url)
        return response.status_code, (time.perf_counter() - started) * 1000
