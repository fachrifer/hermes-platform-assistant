from __future__ import annotations

import httpx

from office_gateway.tools.core import UPSTREAM_TIMEOUT_SECONDS, Param, Tool, ToolError

LLM = frozenset({"llm"})


async def _litellm_get(ctx, path: str, params: dict | None = None):
    url = ctx.config.litellm_url
    key = ctx.config.litellm_master_key
    if not url or not key:
        raise ToolError("not_configured", "LiteLLM URL or master key not set on the gateway")
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_SECONDS) as client:
        response = await client.get(
            f"{url.rstrip('/')}/{path.lstrip('/')}",
            headers={"Authorization": f"Bearer {key}"},
            params=params,
        )
    if response.status_code == 401:
        raise ToolError("invalid_credential", "LiteLLM rejected the master key")
    if response.status_code == 403:
        raise ToolError("forbidden", "LiteLLM key lacks scope for this endpoint")
    if response.status_code >= 400:
        raise ToolError("http_error", f"LiteLLM {path}: HTTP {response.status_code}")
    return response.json()


async def _llm_spend(ctx, args, role):
    endpoint = args.get("endpoint") or "global/spend"
    limit = args.get("limit")

    data = await _litellm_get(ctx, endpoint, {"limit": limit} if limit else None)

    # For spend/keys: strip each key object to essential fields only.
    # Full objects are ~2KB each; 110+ keys exceed the 50KB tool cap.
    # Slim format (~50 bytes each) fits 1000+ keys.
    if endpoint == "spend/keys" and isinstance(data, list):
        slim = [
            {
                "key_alias": k.get("key_alias") or k.get("key_name", "?")[:12],
                "spend": round(k.get("spend", 0), 2),
                "max_budget": k.get("max_budget"),
            }
            for k in data
        ]
        total = round(sum(k["spend"] for k in slim), 2)
        return {"count": len(slim), "total_spend": total, "keys": slim}

    return data


TOOLS = (
    Tool(
        "llm_spend",
        LLM,
        "Query LiteLLM Admin API for spend data. spend/keys returns slim {key_alias, spend, max_budget} + total.",
        {
            "endpoint": Param("string", "which LiteLLM endpoint to query", required=True,
                              enum=("global/spend", "spend/keys", "model/info", "metrics")),
            "limit": Param("number", "max results (passed to API)", minimum=1, maximum=500),
        },
        _llm_spend,
    ),
)