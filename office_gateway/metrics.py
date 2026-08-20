from __future__ import annotations

import httpx

MAX_QUERY_CHARS = 500


async def instant_query(metrics_url: str, query: str) -> dict:
    if not metrics_url:
        raise ValueError("OFFICE_METRICS_URL tidak diisi")
    q = (query or "").strip()
    if not q or len(q) > MAX_QUERY_CHARS:
        raise ValueError("query tidak valid")
    # Deny obvious secret-scraping label selectors by refusing `password|token|api_key`
    lowered = q.lower()
    for banned in ("password", "token", "api_key", "secret", "authorization"):
        if banned in lowered:
            raise ValueError("query not allowlisted")
    url = f"{metrics_url.rstrip('/')}/api/v1/query"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, params={"query": q})
    response.raise_for_status()
    payload = response.json()
    # Pass through Prometheus instant-query JSON only (status/data)
    return {"status": payload.get("status"), "data": payload.get("data")}
