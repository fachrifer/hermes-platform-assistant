"""Allowlisted write adapters (HTTP POST to configured endpoints)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class WriteResult:
    ok: bool
    detail: str


class WriteAdapter:
    """Execute allowlisted writes via configured HTTP endpoints.

    If no endpoint is configured for (target, action), returns a clear
    "adapter not configured" failure without mutating anything.
    """

    def __init__(
        self,
        endpoints: dict[tuple[str, str], str],
        *,
        client: httpx.AsyncClient | None = None,
    ):
        self.endpoints = endpoints
        self._client = client

    async def execute(self, action: str, target: str, params: dict) -> WriteResult:
        url = self.endpoints.get((target, action))
        if url is None:
            return WriteResult(
                ok=False,
                detail=f"adapter not configured for {target}.{action}",
            )

        body = {"action": action, "target": target, "params": params}
        try:
            if self._client is not None:
                response = await self._client.post(url, json=body)
            else:
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
                    response = await client.post(url, json=body)
        except (httpx.HTTPError, OSError) as exc:
            return WriteResult(ok=False, detail=f"adapter request failed: {exc}")

        if 200 <= response.status_code < 300:
            return WriteResult(
                ok=True,
                detail=f"adapter ok ({response.status_code})",
            )
        return WriteResult(
            ok=False,
            detail=f"adapter rejected ({response.status_code}): {response.text[:500]}",
        )
