from __future__ import annotations

import asyncio

from office_gateway.brief import FLEET_AGENTS
from office_gateway.source_view import gateway_roots, read_source
from office_gateway.tools.common import pending_view
from office_gateway.tools.core import Param, Tool, ToolError

SUPERVISOR = frozenset({"supervisor"})

_RANK = {"ok": 0, "unknown": 1, "warn": 2, "critical": 3}


async def _fleet_status(ctx, args, role):
    services = await ctx.collector.collect()
    by_name = {s.get("name"): s for s in services}
    lines = []
    for agent in FLEET_AGENTS:
        ready = by_name.get(agent["ready_service"], {}).get("status", "unknown")
        found = [by_name[n] for n in agent["service_names"] if n in by_name]
        worst = max((s.get("status", "unknown") for s in found), key=lambda s: _RANK.get(s, 1), default="unknown")
        bad = [f"{s['name']}={s.get('status')}" for s in found if s.get("status") != "ok"][:4]
        line = f"{agent['id']}: agent {ready}, services {worst if found else 'n/a'}"
        if bad:
            line += f" ({', '.join(bad)})"
        lines.append(line)
    pending = ctx.store.list_actions(("pending",), 50)
    return {"domains": lines, "pending_approvals": len(pending)}


async def _read_gateway_file(ctx, args, role):
    try:
        return read_source(gateway_roots(), args["path"])
    except ValueError as exc:
        raise ToolError("invalid_argument", str(exc)) from exc


async def _propose_script(ctx, args, role):
    action = await asyncio.to_thread(
        ctx.actions.propose,
        role,
        "add_script",
        args["path"],
        {
            "path": args["path"],
            "content": args["content"],
            "replace": bool(args.get("replace")),
            "reason": args["reason"],
        },
    )
    return pending_view(action, ctx.config.console_url)


async def _propose_gateway_restart(ctx, args, role):
    action = await asyncio.to_thread(
        ctx.actions.propose,
        role,
        "restart_gateway",
        "office-gateway",
        {"reason": args["reason"]},
    )
    return pending_view(action, ctx.config.console_url)


async def _propose_mcp_change(ctx, args, role):
    action = await asyncio.to_thread(
        ctx.actions.propose,
        role,
        "add_mcp_tool",
        args["name"],
        {
            "name": args["name"],
            "summary": args["summary"],
            "spec": args["spec"],
            "source": args["source"],
            "replace": bool(args.get("replace")),
            "reason": args["reason"],
        },
    )
    return pending_view(action, ctx.config.console_url)


TOOLS = (
    Tool(
        "fleet_status",
        frozenset({"supervisor"}),
        "One line per domain (agent health + domain services) and the number of pending approvals. "
        "Answer status questions from this without messaging specialists.",
        {},
        _fleet_status,
    ),
    Tool(
        "propose_mcp_change",
        SUPERVISOR,
        "Propose one new MCP module for any specialist. source is one Python module that assigns TOOLS "
        "(Tool from office_gateway.tools.core). The name must be one of those tools. Handlers are async "
        "and return a dict. A write tool also assigns ACTIONS = {action: {role, propose, execute}}. "
        "propose(service, role, target, params, reason) calls service.store.propose. "
        "execute(service, claimed) returns service._finish. Read tools omit ACTIONS. "
        "Any existing agent role is allowed. Set replace true to change a tool that already exists. "
        "Supervisor tools cannot be replaced. "
        "A person approves the source; the gateway installs the module and restarts. "
        "Read the current module with read_gateway_file before writing source. "
        "spec is a short description that includes the tool name.",
        {
            "name": Param("string", "tool name, snake_case, also the module name", required=True, max_length=64),
            "summary": Param("string", "one line shown on the Approvals page", required=True, max_length=200),
            "spec": Param("string", "short description of the tool, including its name", required=True, max_length=6000),
            "source": Param("string", "the Python module that assigns TOOLS", required=True, max_length=24000),
            "reason": Param("string", "why the tool is needed", required=True, max_length=200),
            "replace": Param("boolean", "true replaces a built-in tool of the same name", default=False),
        },
        _propose_mcp_change,
    ),
    Tool(
        "read_gateway_file",
        SUPERVISOR,
        "Read one gateway module or script before proposing a change. "
        "path starts with office_gateway/ or scripts/, for example office_gateway/tools/llm.py.",
        {"path": Param("string", "repo path of a .py, .sh, .md, or .yml file", required=True, max_length=200)},
        _read_gateway_file,
    ),
    Tool(
        "propose_gateway_restart",
        SUPERVISOR,
        "Propose a restart of the office gateway. Nothing restarts until a person approves it. "
        "A tool install already restarts the gateway, so use this only when a restart is the whole change.",
        {"reason": Param("string", "why the gateway should restart", required=True, max_length=200)},
        _propose_gateway_restart,
    ),
    Tool(
        "propose_script",
        SUPERVISOR,
        "Propose a new script under scripts/. path is scripts/<name>.sh or scripts/<name>.py. "
        "content is the whole file. replace true overwrites an existing file. "
        "Nothing is written until a person approves it.",
        {
            "path": Param("string", "scripts/<name>.sh or scripts/<name>.py", required=True, max_length=80),
            "content": Param("string", "full text of the new script", required=True, max_length=12000),
            "reason": Param("string", "why this script is needed", required=True, max_length=200),
            "replace": Param("boolean", "true overwrites an existing script of the same name", default=False),
        },
        _propose_script,
    ),
)
