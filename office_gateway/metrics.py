from __future__ import annotations

import re
from typing import Any

import httpx

MAX_QUERY_CHARS = 500
MAX_RESULT_SERIES = 50
MAX_RESULT_SAMPLES = 50
_SENSITIVE_LABEL_RE = re.compile(
    r"password|passwd|token|secret|api_key|authorization|credential|bearer|private_key",
    re.IGNORECASE,
)
_BANNED_QUERY_SUBSTRINGS = (
    "password",
    "passwd",
    "token",
    "api_key",
    "secret",
    "authorization",
    "credential",
    "bearer",
    "private_key",
)


def _sanitize_series(series: dict[str, Any]) -> dict[str, Any]:
    metric = series.get("metric")
    safe_metric = {}
    if isinstance(metric, dict):
        safe_metric = {
            str(key): str(value)
            for key, value in metric.items()
            if not _SENSITIVE_LABEL_RE.search(str(key))
        }
    sanitized: dict[str, Any] = {"metric": safe_metric}
    if "value" in series:
        sanitized["value"] = series["value"]
    elif "values" in series:
        values = series["values"]
        if isinstance(values, list):
            sanitized["values"] = values[:MAX_RESULT_SAMPLES]
        else:
            sanitized["values"] = values
    return sanitized


def sanitize_metrics_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_data = payload.get("data")
    if not isinstance(raw_data, dict):
        raw_data = {}
    result_type = raw_data.get("resultType")
    result = raw_data.get("result")
    if result_type in {"vector", "matrix"} and isinstance(result, list):
        safe_result = [
            _sanitize_series(series)
            for series in result[:MAX_RESULT_SERIES]
            if isinstance(series, dict)
        ]
    else:
        safe_result = result
    return {
        "status": payload.get("status"),
        "data": {
            "resultType": result_type,
            "result": safe_result,
        },
    }


async def instant_query(metrics_url: str, query: str) -> dict:
    if not metrics_url:
        raise ValueError("OFFICE_METRICS_URL tidak diisi")
    q = (query or "").strip()
    if not q or len(q) > MAX_QUERY_CHARS:
        raise ValueError("query tidak valid")
    # Deny obvious secret-scraping label selectors by refusing `password|token|api_key`
    lowered = q.lower()
    for banned in _BANNED_QUERY_SUBSTRINGS:
        if banned in lowered:
            raise ValueError("query not allowlisted")
    url = f"{metrics_url.rstrip('/')}/api/v1/query"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, params={"query": q})
    response.raise_for_status()
    payload = response.json()
    return sanitize_metrics_payload(payload)
