"""Narrow laptop relay that forwards signed observer envelopes to Hermes Cloud."""

from __future__ import annotations

import datetime as dt
from typing import Protocol
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

MAX_ENVELOPE_BYTES = 65_536


class CloudForwarder(Protocol):
    async def forward(self, body: bytes, headers: dict[str, str]) -> tuple[int, dict]: ...


class OfficeHours:
    """Fail-closed office-hour policy for the laptop relay."""

    def __init__(self, timezone: str, start: str, end: str):
        self.timezone = ZoneInfo(timezone)
        self.start = dt.time.fromisoformat(start)
        self.end = dt.time.fromisoformat(end)
        if self.start >= self.end:
            raise ValueError("jam relay harus berada dalam satu hari kerja")

    def is_open(self, now: dt.datetime) -> bool:
        local_now = now.replace(tzinfo=self.timezone) if now.tzinfo is None else now.astimezone(self.timezone)
        return self.start <= local_now.time() < self.end


def create_app(
    forwarder: CloudForwarder,
    *,
    office_hours: OfficeHours | None = None,
    now: callable = dt.datetime.now,
) -> FastAPI:
    app = FastAPI(title="Hermes Office Relay", docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/v1/relay/snapshots")
    async def relay_snapshot(request: Request):
        if office_hours is None or not office_hours.is_open(now()):
            raise HTTPException(status_code=503, detail="Relay di luar jam kantor")
        body = await request.body()
        if len(body) > MAX_ENVELOPE_BYTES:
            raise HTTPException(status_code=413, detail="Snapshot terlalu besar")
        headers = {
            "x-hermes-observer": request.headers.get("x-hermes-observer", ""),
            "x-hermes-signature": request.headers.get("x-hermes-signature", ""),
        }
        if not all(headers.values()):
            raise HTTPException(status_code=401, detail="Observer envelope tidak lengkap")
        status_code, payload = await forwarder.forward(body, headers)
        return JSONResponse(payload, status_code=status_code)

    return app
