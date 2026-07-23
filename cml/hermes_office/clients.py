"""HTTP clients and file log sources for CML hermes-office."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx

from office_observer.collectors import HttpServiceCollector


class FileLogSource:
    """Read the last N lines from configured local log files."""

    def __init__(self, paths: dict[str, str], *, max_lines: int = 100):
        self.paths = paths
        self.max_lines = max_lines

    async def collect(self) -> list[dict]:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        entries: list[dict] = []
        for service, path in self.paths.items():
            file_path = Path(path)
            if not file_path.is_file():
                continue
            try:
                lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines[-self.max_lines :]:
                text = line.strip()
                if text:
                    entries.append({"service": service, "ts": now, "line": text[:500]})
        return entries


class OpenAIChatClient:
    """Minimal OpenAI-compatible chat client for local Qwen."""

    def __init__(self, base_url: str, *, model: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def chat(self, messages: list[dict]) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.2,
                },
            )
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]["content"]


class HermesCloudReportClient:
    def __init__(self, url: str, *, observer_id: str, timeout: float = 30.0):
        self.url = url
        self.observer_id = observer_id
        self.timeout = timeout

    async def post_report(self, payload: dict, signature: str) -> bool:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.url,
                json=payload,
                headers={
                    "X-Hermes-Observer": self.observer_id,
                    "X-Hermes-Signature": signature,
                },
            )
        return response.status_code in {200, 202}


def build_collector(service_urls: dict[str, str]) -> HttpServiceCollector:
    return HttpServiceCollector(service_urls)
