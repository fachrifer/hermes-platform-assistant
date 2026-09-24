from __future__ import annotations

from office_gateway.brief import FLEET_AGENTS
from office_gateway.tools.core import Tool

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


TOOLS = (
    Tool(
        "fleet_status",
        frozenset({"supervisor"}),
        "One line per domain (agent health + domain services) and the number of pending approvals. "
        "Answer status questions from this without messaging specialists.",
        {},
        _fleet_status,
    ),
)
