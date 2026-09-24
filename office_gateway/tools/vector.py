from __future__ import annotations

import asyncio
import time

import httpx

from office_gateway.tools.core import UPSTREAM_TIMEOUT_SECONDS, Param, Tool, ToolError

VECTOR = frozenset({"vector"})


async def _probe(client: httpx.AsyncClient, name: str, url: str) -> dict:
    start = time.monotonic()
    try:
        response = await client.get(url)
    except httpx.TimeoutException:
        return {"instance": name, "status": "timeout"}
    except httpx.HTTPError:
        return {"instance": name, "status": "unreachable"}
    return {
        "instance": name,
        "status": "up" if response.status_code < 400 else "down",
        "http": response.status_code,
        "latency_ms": int((time.monotonic() - start) * 1000),
    }


async def _vector_status(ctx, args, role):
    instances = ctx.config.vector_instances
    if not instances:
        raise ToolError("not_configured", "OFFICE_VECTOR_INSTANCES is empty")
    wanted = args.get("instance")
    if wanted and wanted not in instances:
        raise ToolError("invalid_argument", "unknown instance", sorted(instances))
    chosen = {wanted: instances[wanted]} if wanted else instances
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_SECONDS) as client:
        rows = await asyncio.gather(*(_probe(client, n, u) for n, u in chosen.items()))
    return {"instances": list(rows)}


TOOLS = (
    Tool(
        "vector_status",
        VECTOR,
        "Health of the vector databases: milvus-dev (Lab VM), milvus-prod (10.216.203.132, read-only), qdrant-dev.",
        {"instance": Param("string", "one instance; omit for all", max_length=32)},
        _vector_status,
    ),
)
