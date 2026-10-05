from __future__ import annotations

from office_gateway.actions import action_view
from office_gateway.redact import redact_text
from office_gateway.tools.core import Param, Tool

LINE_MAX = 200


def logs_window(lines: list[str], limit: int, contains: str | None, budget: int = 1500) -> dict:
    rows = [redact_text(line)[:LINE_MAX] for line in lines]
    if contains:
        needle = contains.lower()
        rows = [row for row in rows if needle in row.lower()]
    rows = rows[-limit:]
    kept: list[str] = []
    used = 0
    for row in reversed(rows):
        if used + len(row) + 4 > budget:
            break
        kept.append(row)
        used += len(row) + 4
    kept.reverse()
    return {"lines": kept, "omitted": len(rows) - len(kept)}


def pending_view(action, console_url: str) -> dict:
    return {
        "action_id": action.action_id,
        "status": action.status,
        "summary": action.summary,
        "expires_at": action.expires_at,
        "approve_at": f"{console_url}/approvals/" if console_url else "/approvals/",
    }


async def _action_status(ctx, args, role):
    view = action_view(ctx.actions.status(role, args["action_id"]))
    return {k: view[k] for k in ("action_id", "status", "summary", "approver", "detail", "result")}


ACTION_STATUS = Tool(
    name="action_status",
    roles=frozenset({"supervisor", "lab-host", "ingress", "obs"}),
    description=(
        "Status of an action you proposed: pending, executing, succeeded, failed, rejected or expired. "
        "Only a human can approve; call this at most once per user request."
    ),
    params={"action_id": Param("string", "action_id returned by a propose_* tool", required=True, max_length=64)},
    handler=_action_status,
)

TOOLS = (ACTION_STATUS,)
