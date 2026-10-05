"""Approved MCP modules live next to the gateway database and load on each request."""

from __future__ import annotations

import ast
import re
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from office_gateway.roles import AGENT_ROLES
from office_gateway.tools.core import Tool

MAX_SOURCE = 24000
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
PROTECTED_TOOLS = frozenset(
    {
        "fleet_status",
        "propose_mcp_change",
        "action_status",
        "read_gateway_file",
        "propose_gateway_restart",
        "propose_script",
    }
)
BUILTIN_ACTIONS = frozenset(
    {
        "restart_service",
        "apply_edge_routes",
        "rollback_edge_routes",
        "create_dashboard",
        "archive_dashboard",
        "add_mcp_tool",
        "add_script",
    }
)


class PluginError(Exception):
    def __init__(self, message: str, category: str = "invalid_argument") -> None:
        super().__init__(message)
        self.category = category
        self.detail = message


@dataclass(frozen=True)
class PluginAction:
    name: str
    role: str
    propose: Callable
    execute: Callable


@dataclass(frozen=True)
class LoadedPlugins:
    tools: tuple[Tool, ...]
    actions: dict[str, PluginAction]


def active_registry(config) -> dict[str, Tool]:
    from office_gateway.tools import REGISTRY

    merged = dict(REGISTRY)
    loaded = load_plugins(plugin_dir(config), set(REGISTRY))
    for tool in loaded.tools:
        if tool.name not in PROTECTED_TOOLS:
            merged[tool.name] = tool
    return merged


def plugin_dir(config) -> Path:
    return Path(config.db_path).parent / "mcp_plugins"


def validate_source(name: str, source: str, builtin_tools: set[str], replace: bool = False) -> None:
    if not _NAME.fullmatch(name or ""):
        raise PluginError("tool name must be a short snake_case identifier")
    if name in PROTECTED_TOOLS:
        raise PluginError("that tool cannot be replaced")
    if name in builtin_tools and not replace:
        raise PluginError("that tool already exists")
    if not source or not source.strip():
        raise PluginError("source is required")
    if len(source) > MAX_SOURCE:
        raise PluginError("source is too long")
    if "\x00" in source:
        raise PluginError("source is invalid")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise PluginError(f"source is not valid Python: {exc.msg}") from exc
    assigned = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigned.add(node.target.id)
    if "TOOLS" not in assigned:
        raise PluginError("source must assign TOOLS")
    if not any(isinstance(node, ast.Constant) and node.value == name for node in ast.walk(tree)):
        raise PluginError("source must include the tool name")
    compile(tree, f"{name}.py", "exec")


def install_plugin(directory: Path, name: str, source: str, builtin_tools: set[str], replace: bool = False) -> Path:
    validate_source(name, source, builtin_tools, replace=replace)
    loaded = _load_source(source, name, builtin_tools, replace=replace)
    directory.mkdir(parents=True, exist_ok=True)
    _reject_collisions(directory, name, loaded, builtin_tools)
    target = directory / f"{name}.py"
    temporary = directory / f".{name}.writing"
    temporary.write_text(source, encoding="utf-8", newline="\n")
    if target.exists():
        target.with_suffix(".py.bak").write_bytes(target.read_bytes())
    temporary.replace(target)
    return target


def load_plugins(directory: Path, builtin_tools: set[str]) -> LoadedPlugins:
    tools: list[Tool] = []
    actions: dict[str, PluginAction] = {}
    seen = set(PROTECTED_TOOLS)
    if not directory.is_dir():
        return LoadedPlugins((), {})
    for path in sorted(directory.glob("*.py")):
        if not _NAME.fullmatch(path.stem):
            continue
        try:
            loaded = _load_file(path, path.stem, builtin_tools, replace=True)
        except PluginError:
            continue
        if any(tool.name in seen for tool in loaded.tools) or any(name in actions or name in BUILTIN_ACTIONS for name in loaded.actions):
            continue
        tools.extend(loaded.tools)
        actions.update(loaded.actions)
        seen.update(tool.name for tool in loaded.tools)
    return LoadedPlugins(tuple(tools), actions)


def _reject_collisions(directory: Path, name: str, loaded: LoadedPlugins, builtin_tools: set[str]) -> None:
    if not directory.is_dir():
        return
    for path in directory.glob("*.py"):
        if path.stem == name or not _NAME.fullmatch(path.stem):
            continue
        try:
            other = _load_file(path, path.stem, builtin_tools, replace=True)
        except PluginError:
            continue
        other_tools = {tool.name for tool in other.tools}
        for tool in loaded.tools:
            if tool.name in other_tools:
                raise PluginError(f"tool {tool.name} is already provided by another module")
        for action_name in loaded.actions:
            if action_name in other.actions:
                raise PluginError(f"action {action_name} is already provided by another module")


def _load_file(path: Path, stem: str, builtin_tools: set[str], replace: bool = False) -> LoadedPlugins:
    return _load_source(path.read_text(encoding="utf-8"), stem, builtin_tools, replace=replace)


def _load_source(source: str, stem: str, builtin_tools: set[str], replace: bool = False) -> LoadedPlugins:
    if not _NAME.fullmatch(stem):
        raise PluginError("tool name must be a short snake_case identifier")
    module_name = f"office_mcp_plugin_{stem}"
    sys.modules.pop(module_name, None)
    module = types.ModuleType(module_name)
    module.__file__ = f"{stem}.py"
    sys.modules[module_name] = module
    try:
        exec(compile(source, f"{stem}.py", "exec"), module.__dict__)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise PluginError(f"{type(exc).__name__}: {exc}") from exc
    return _interpret(module, stem, builtin_tools, replace=replace)


def _interpret(module: Any, stem: str, builtin_tools: set[str], replace: bool = False) -> LoadedPlugins:
    raw_tools = getattr(module, "TOOLS", None)
    if not isinstance(raw_tools, (tuple, list)) or not raw_tools:
        raise PluginError("TOOLS must list at least one tool")
    tools: list[Tool] = []
    for item in raw_tools:
        if not isinstance(item, Tool):
            raise PluginError("TOOLS must contain Tool values")
        if not _NAME.fullmatch(item.name):
            raise PluginError(f"tool name {item.name} is invalid")
        if item.name in PROTECTED_TOOLS:
            raise PluginError(f"tool {item.name} cannot be replaced")
        if item.name in builtin_tools and not replace:
            raise PluginError(f"tool {item.name} already exists")
        if not item.roles or not set(item.roles) <= AGENT_ROLES:
            raise PluginError(f"tool {item.name} must name an existing agent role")
        if not callable(item.handler):
            raise PluginError(f"tool {item.name} has no handler")
        tools.append(item)
    if stem not in {tool.name for tool in tools}:
        raise PluginError("source must define the named tool")
    actions: dict[str, PluginAction] = {}
    raw_actions = getattr(module, "ACTIONS", None)
    if raw_actions is not None:
        if not isinstance(raw_actions, dict):
            raise PluginError("ACTIONS must be a dict")
        for action_name, spec in raw_actions.items():
            if not isinstance(action_name, str) or not _NAME.fullmatch(action_name):
                raise PluginError("action name is invalid")
            if action_name in BUILTIN_ACTIONS:
                raise PluginError(f"action {action_name} already exists")
            if not isinstance(spec, dict):
                raise PluginError(f"action {action_name} must map role, propose, and execute")
            role = spec.get("role")
            propose = spec.get("propose")
            execute = spec.get("execute")
            if role not in AGENT_ROLES or not callable(propose) or not callable(execute):
                raise PluginError(f"action {action_name} must map role, propose, and execute")
            actions[action_name] = PluginAction(action_name, str(role), propose, execute)
    return LoadedPlugins(tuple(tools), actions)
