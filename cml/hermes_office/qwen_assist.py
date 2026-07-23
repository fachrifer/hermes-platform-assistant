"""Qwen3 assist for log selection and recommendations."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger("hermes.cml.qwen_assist")

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class ChatClient(Protocol):
    async def chat(self, messages: list[dict]) -> str: ...


@dataclass(frozen=True)
class AssistResult:
    available: bool
    model: str
    selected_logs: list[dict]
    recommendation: str


class QwenAssist:
    def __init__(self, client: ChatClient, *, model: str = "Qwen3-8B", max_logs: int = 10):
        self.client = client
        self.model = model
        self.max_logs = max_logs

    async def select_and_recommend(
        self, *, services: list[dict], logs: list[dict]
    ) -> AssistResult:
        capped = logs[-200:]
        prompt = {
            "services": services,
            "logs": capped,
            "instructions": (
                "Select up to 10 important log lines and write one short operational "
                "recommendation. Reply with JSON only: "
                '{"selected_logs":[{"service":"...","ts":"...","line":"..."}],'
                '"recommendation":"..."}'
            ),
        }
        try:
            raw = await self.client.chat(
                [
                    {
                        "role": "system",
                        "content": "You are an infrastructure assistant. Reply with JSON only.",
                    },
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=True)},
                ]
            )
        except Exception:  # noqa: BLE001
            logger.exception("Qwen assist unavailable")
            return AssistResult(
                available=False,
                model=self.model,
                selected_logs=[],
                recommendation="qwen unavailable",
            )
        parsed = self._parse(raw)
        selected = parsed.get("selected_logs") or []
        if not isinstance(selected, list):
            selected = []
        clean_logs: list[dict] = []
        for item in selected[: self.max_logs]:
            if not isinstance(item, dict):
                continue
            service = item.get("service")
            ts = item.get("ts")
            line = item.get("line")
            if isinstance(service, str) and isinstance(ts, str) and isinstance(line, str) and line:
                clean_logs.append({"service": service, "ts": ts, "line": line[:500]})
        recommendation = parsed.get("recommendation")
        if not isinstance(recommendation, str) or not recommendation.strip():
            recommendation = "No recommendation produced."
        return AssistResult(
            available=True,
            model=self.model,
            selected_logs=clean_logs,
            recommendation=recommendation.strip()[:4000],
        )

    @staticmethod
    def _parse(raw: str) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = _JSON_RE.search(raw or "")
            if not match:
                return {}
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}
