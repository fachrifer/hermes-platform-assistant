from __future__ import annotations

from office_gateway.tools.core import Tool

ALL_TOOLS: tuple[Tool, ...] = ()

REGISTRY: dict[str, Tool] = {tool.name: tool for tool in ALL_TOOLS}


def tools_for_role(role: str) -> list[Tool]:
    return [tool for tool in ALL_TOOLS if role in tool.roles]
