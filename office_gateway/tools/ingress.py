from __future__ import annotations

import asyncio

from office_gateway.edge_ops import route_diff
from office_gateway.tls_ops import tls_cert_status
from office_gateway.tools.common import logs_window, pending_view
from office_gateway.tools.core import Param, Tool, ToolError

INGRESS = frozenset({"ingress"})
_CONTENT = Param(
    "string",
    "full edge-routes file: one route per line `name /path/ host:port websocket(0|1) [strip_prefix(0|1)]`",
    required=True,
    max_length=8000,
)
_REASON = Param("string", "one-line reason shown to the approver", required=True, max_length=200)
_LINES = Param("integer", "number of newest log lines (1-100)", minimum=1, maximum=100, default=30)


def _edge_containers(ctx) -> list[dict]:
    listing = ctx.docker.list_containers()
    return [c for c in listing.get("containers", []) if c.get("compose_service") == "office-edge"]


async def _list_routes(ctx, args, role):
    routes = await asyncio.to_thread(ctx.edge.list_routes)
    return {
        "count": len(routes),
        "routes": [
            {"name": r.name, "path": r.path, "upstream": r.upstream, "websocket": r.websocket}
            for r in routes
        ][:40],
        "backups": ctx.edge.list_backups()[:10],
    }


async def _edge_status(ctx, args, role):
    containers = await asyncio.to_thread(_edge_containers, ctx)
    tls = tls_cert_status(ctx.config.edge_tls_cert_path)
    return {
        "configured": ctx.edge.configured(),
        "edge": [{"name": c["name"], "status": c["status"], "health": c.get("health")} for c in containers],
        "tls_days_left": tls.get("days_left"),
        "tls_error": tls.get("error"),
    }


async def _tls_status(ctx, args, role):
    return tls_cert_status(ctx.config.edge_tls_cert_path)


async def _tail_traefik_logs(ctx, args, role):
    containers = await asyncio.to_thread(_edge_containers, ctx)
    if not containers:
        raise ToolError("not_configured", "office-edge container not found")
    name = containers[0]["name"]
    fetch = 500 if args.get("contains") else args["lines"]
    raw = await asyncio.to_thread(ctx.docker.logs, name, fetch)
    return {"name": name, **logs_window(raw.get("lines", []), args["lines"], args.get("contains"))}


async def _validate_route_change(ctx, args, role):
    try:
        routes = ctx.edge.validate(args["content"])
    except ValueError as exc:
        raise ToolError("invalid_argument", str(exc)) from exc
    return {
        "valid": True,
        "routes": len(routes),
        "diff": route_diff(ctx.edge.read_text_or_empty(), args["content"], limit=30),
    }


async def _propose_route_change(ctx, args, role):
    action = await asyncio.to_thread(
        ctx.actions.propose,
        role,
        "apply_edge_routes",
        "edge-routes",
        {"content": args["content"], "reason": args["reason"]},
    )
    return {**pending_view(action, ctx.config.console_url), "diff": action.params.get("diff", [])[:30]}


async def _propose_route_rollback(ctx, args, role):
    action = await asyncio.to_thread(
        ctx.actions.propose,
        role,
        "rollback_edge_routes",
        "edge-routes",
        {"backup": args.get("backup"), "reason": args["reason"]},
    )
    return {**pending_view(action, ctx.config.console_url), "diff": action.params.get("diff", [])[:30]}


TOOLS = (
    Tool("list_routes", INGRESS, "Parsed Lab-VM Traefik edge routes and the newest route backups.", {}, _list_routes),
    Tool("edge_status", INGRESS, "office-edge (Traefik) container state and TLS days left.", {}, _edge_status),
    Tool("tls_status", INGRESS, "Lab edge TLS certificate metadata (subject, expiry). No private key.", {}, _tls_status),
    Tool(
        "tail_traefik_logs",
        INGRESS,
        "Newest redacted office-edge (Traefik) log lines. Use contains= (e.g. 502) to filter.",
        {"lines": _LINES, "contains": Param("string", "optional substring filter", max_length=60)},
        _tail_traefik_logs,
    ),
    Tool(
        "validate_route_change",
        INGRESS,
        "Check a full edge-routes file and show the diff against the current file. Changes nothing.",
        {"content": _CONTENT},
        _validate_route_change,
    ),
    Tool(
        "propose_route_change",
        INGRESS,
        "Propose replacing edge-routes with this content. Creates a pending action for a human to approve; "
        "the current file is backed up and restored automatically if the apply fails.",
        {"content": _CONTENT, "reason": _REASON},
        _propose_route_change,
    ),
    Tool(
        "propose_route_rollback",
        INGRESS,
        "Propose restoring the newest (or a named) edge-routes backup. Needs human approval.",
        {"backup": Param("string", "backup name from list_routes; default newest", max_length=64), "reason": _REASON},
        _propose_route_rollback,
    ),
)
