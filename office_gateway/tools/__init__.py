from __future__ import annotations

from office_gateway.tools import cluster, common, ingress, lab_host, llm, obs, status, vector
from office_gateway.tools.core import Tool

ALL_TOOLS: tuple[Tool, ...] = (
    *status.TOOLS,
    *common.TOOLS,
    *lab_host.TOOLS,
    *ingress.TOOLS,
    *llm.TOOLS,
    *cluster.TOOLS,
    *vector.TOOLS,
    *obs.TOOLS,
)

REGISTRY: dict[str, Tool] = {tool.name: tool for tool in ALL_TOOLS}
assert len(REGISTRY) == len(ALL_TOOLS), "duplicate tool names"


def tools_for_role(role: str) -> list[Tool]:
    return [tool for tool in ALL_TOOLS if role in tool.roles]
