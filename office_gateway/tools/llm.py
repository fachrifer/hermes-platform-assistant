from __future__ import annotations

from office_gateway.llm_ops import fetch_litellm_status
from office_gateway.tools.core import Tool, ToolError

LLM = frozenset({"llm"})


async def _status(ctx):
    data = await fetch_litellm_status(ctx.config.litellm_url, ctx.config.litellm_master_key)
    if not data.get("configured"):
        raise ToolError("not_configured", "LiteLLM URL or master key not set on the gateway")
    return data


async def _llm_status(ctx, args, role):
    data = await _status(ctx)
    return {k: data.get(k) for k in ("ok", "auth", "models", "keys", "error") if k in data}


async def _list_models(ctx, args, role):
    data = await _status(ctx)
    return {"count": data.get("models", 0), "models": data.get("model_ids", [])[:20]}


TOOLS = (
    Tool("llm_status", LLM, "LiteLLM prod API health: auth, model count, virtual key count.", {}, _llm_status),
    Tool("list_models", LLM, "Model aliases served by LiteLLM prod (max 20).", {}, _list_models),
)
