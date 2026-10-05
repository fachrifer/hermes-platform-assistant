from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from office_gateway.mcp_plugins import active_registry
from office_gateway.roles import AGENT_ROLES
from office_gateway.tools.core import ToolContext, call_tool

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-11-25", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "office-gateway", "version": "2026.9-phase1"}
INSTRUCTIONS = (
    "Office Lab tools. Every result is JSON {ok, data} or {ok:false, error:{category, detail, valid}}. "
    "Arguments are bounded; on invalid_argument pick from `valid`. propose_* tools never execute: "
    "a human approves in the console."
)


def _reply(id_: Any, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result})


def _error(id_: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})


def build_router(ctx: ToolContext, role_dependency: Callable[..., str]) -> APIRouter:
    router = APIRouter()

    @router.post("/mcp")
    async def mcp(request: Request, role: str = Depends(role_dependency)) -> Response:
        if role not in AGENT_ROLES:
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        try:
            message = json.loads(await request.body() or b"null")
        except ValueError:
            return _error(None, -32700, "parse error")
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
            return _error(None, -32600, "invalid request")
        method = str(message["method"])
        id_ = message.get("id")
        params = message.get("params") or {}
        if id_ is None:
            return Response(status_code=202)
        if method == "initialize":
            requested = str(params.get("protocolVersion") or "")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
            return _reply(
                id_,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                    "instructions": INSTRUCTIONS,
                },
            )
        if method == "ping":
            return _reply(id_, {})
        if method == "tools/list":
            registry = active_registry(ctx.config)
            tools = [
                {"name": t.name, "description": t.description, "inputSchema": t.input_schema()}
                for t in registry.values()
                if role in t.roles
            ]
            return _reply(id_, {"tools": tools})
        if method == "tools/call":
            name = str(params.get("name") or "")
            envelope = await call_tool(ctx, active_registry(ctx.config), role, name, params.get("arguments") or {})
            text = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), default=str)
            return _reply(id_, {"content": [{"type": "text", "text": text}], "isError": not envelope["ok"]})
        return _error(id_, -32601, f"method not found: {method}")

    @router.get("/mcp")
    async def mcp_get() -> Response:
        return Response(status_code=405, headers={"Allow": "POST"})

    @router.delete("/mcp")
    async def mcp_delete() -> Response:
        return Response(status_code=405, headers={"Allow": "POST"})

    return router
