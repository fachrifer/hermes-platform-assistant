"""Tavily web and news search connector (Indonesia-biased, stricter defaults)."""

from __future__ import annotations

import logging
import re

import requests

from config.settings import settings

logger = logging.getLogger("hermes.tavily")

_SEARCH_URL = "https://api.tavily.com/search"
_USAGE_URL = "https://api.tavily.com/usage"

_ID_MARKERS = (
    "indonesia",
    "indonesian",
    "jakarta",
    "nusantara",
    "ri ",
    " nkri",
    ".id",
)


def bias_query_for_indonesia(query: str) -> str:
    """Append Indonesia context when the query is not already locale-anchored."""
    text = query.strip()
    if not text:
        return text
    lowered = f" {text.casefold()} "
    if any(marker in lowered for marker in _ID_MARKERS):
        return text
    if re.search(r"\b(id|idn)\b", text.casefold()):
        return text
    return f"{text} Indonesia"


class TavilyConnector:
    def __init__(self, api_key: str | None = None):
        self.api_key = settings.tavily_api_key if api_key is None else api_key.strip()

    def search(self, query: str, news: bool = False, max_results: int = 5) -> list[dict]:
        if not self.api_key or not query.strip():
            return []
        topic = "news" if news else "general"
        # Country boost is only available for topic=general; always bias the query text.
        effective_query = bias_query_for_indonesia(query)
        payload: dict = {
            "query": effective_query,
            "topic": topic,
            "max_results": max(1, min(max_results, 10)),
            "include_answer": False,
            "search_depth": settings.tavily_search_depth,
        }
        if topic == "general" and settings.tavily_country:
            payload["country"] = settings.tavily_country
        try:
            response = requests.post(
                _SEARCH_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()
            return [
                {
                    "title": item.get("title", "(tanpa judul)"),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                }
                for item in data.get("results", [])
                if item.get("url")
            ]
        except requests.RequestException as exc:
            logger.warning("Tavily search gagal: %s", exc)
            return []

    def usage(self) -> dict:
        if not self.api_key:
            return {}
        try:
            response = requests.get(
                _USAGE_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}
        except requests.RequestException as exc:
            logger.warning("Tavily usage gagal: %s", exc)
            return {}
