from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from office_gateway.actions import ActionError
from office_gateway.edge_ops import AdapterNotConfigured as EdgeAdapterNotConfigured
from office_gateway.k8s_ops import AdapterNotConfigured

RESULT_CAP = 2000
TOOL_TIMEOUT_SECONDS = 12.0
UPSTREAM_TIMEOUT_SECONDS = 10.0
ERROR_CATEGORIES = frozenset(
    {"unreachable", "timeout", "forbidden", "invalid_argument", "no_metrics", "not_configured"}
)


class ToolError(Exception):
    def __init__(self, category: str, detail: str, valid=None) -> None:
        super().__init__(detail)
        self.category = category if category in ERROR_CATEGORIES else "invalid_argument"
        self.detail = detail
        self.valid = list(valid or [])


@dataclass(frozen=True)
class Param:
    type: str
    description: str
    required: bool = False
    enum: tuple = ()
    minimum: int | None = None
    maximum: int | None = None
    max_length: int | None = None
    default: Any = None

    def schema(self) -> dict:
        out: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.enum:
            out["enum"] = list(self.enum)
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        if self.max_length is not None:
            out["maxLength"] = self.max_length
        if self.default is not None:
            out["default"] = self.default
        return out


Handler = Callable[["ToolContext", dict, str], Awaitable[dict]]


@dataclass(frozen=True)
class Tool:
    name: str
    roles: frozenset[str]
    description: str
    params: dict[str, Param]
    handler: Handler

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {name: p.schema() for name, p in self.params.items()},
            "required": [name for name, p in self.params.items() if p.required],
            "additionalProperties": False,
        }


@dataclass
class ToolContext:
    config: Any
    store: Any
    actions: Any
    docker: Any
    edge: Any
    k8s: Any
    collector: Any
    proc: Any
    systemd: Any
    extras: dict = field(default_factory=dict)


def ok(data: dict) -> dict:
    return {"ok": True, "data": data}


def err(category: str, detail: str, valid=None) -> dict:
    error: dict[str, Any] = {"category": category, "detail": str(detail)[:300]}
    if valid:
        error["valid"] = [str(v) for v in list(valid)[:40]]
    return {"ok": False, "error": error}


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))


def cap(envelope: dict) -> dict:
    if _size(envelope) <= RESULT_CAP:
        return envelope
    if not envelope.get("ok"):
        error = envelope.get("error", {})
        return err(error.get("category", "unreachable"), str(error.get("detail", ""))[:200])
    text = json.dumps(envelope.get("data"), ensure_ascii=False, separators=(",", ":"), default=str)
    return ok({"truncated": True, "text": text[: RESULT_CAP - 120]})


def fit_items(items: list, budget: int = 1500) -> tuple[list, int]:
    kept: list = []
    used = 0
    for item in items:
        size = _size(item) + 1
        if used + size > budget:
            break
        kept.append(item)
        used += size
    return kept, len(items) - len(kept)


def validate_args(tool: Tool, args: Any) -> dict:
    if not isinstance(args, dict):
        raise ToolError("invalid_argument", "arguments must be an object")
    unknown = sorted(set(args) - set(tool.params))
    if unknown:
        raise ToolError("invalid_argument", f"unknown argument(s): {', '.join(unknown)}", sorted(tool.params))
    out: dict[str, Any] = {}
    for name, p in tool.params.items():
        value = args.get(name)
        if value is None or (isinstance(value, str) and not value.strip() and not p.required):
            if p.required:
                raise ToolError("invalid_argument", f"{name} is required")
            if p.default is not None:
                out[name] = p.default
            continue
        if p.type == "string":
            if not isinstance(value, str) or not value.strip():
                raise ToolError("invalid_argument", f"{name} must be a non-empty string")
            value = value.strip()
            if p.max_length is not None and len(value) > p.max_length:
                raise ToolError("invalid_argument", f"{name} longer than {p.max_length} characters")
        elif p.type == "integer":
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise ToolError("invalid_argument", f"{name} must be an integer")
            try:
                value = int(value)
            except ValueError as exc:
                raise ToolError("invalid_argument", f"{name} must be an integer") from exc
            if (p.minimum is not None and value < p.minimum) or (p.maximum is not None and value > p.maximum):
                raise ToolError("invalid_argument", f"{name} must be between {p.minimum} and {p.maximum}")
        elif p.type == "boolean":
            if not isinstance(value, bool):
                raise ToolError("invalid_argument", f"{name} must be true or false")
        if p.enum and value not in p.enum:
            raise ToolError("invalid_argument", f"{name} must be one of the valid values", p.enum)
        out[name] = value
    return out


async def call_tool(ctx: ToolContext, registry: dict[str, Tool], role: str, name: str, args: Any) -> dict:
    tool = registry.get(name)
    if tool is None:
        valid = sorted(t.name for t in registry.values() if role in t.roles)
        return err("invalid_argument", f"unknown tool {name}", valid)
    if role not in tool.roles:
        return err("forbidden", f"{name} is not available to {role}")
    try:
        clean = validate_args(tool, args if args is not None else {})
        data = await asyncio.wait_for(tool.handler(ctx, clean, role), TOOL_TIMEOUT_SECONDS)
        return cap(ok(data))
    except ToolError as exc:
        return cap(err(exc.category, exc.detail, exc.valid))
    except ActionError as exc:
        category = exc.category if exc.category in ERROR_CATEGORIES else "invalid_argument"
        return cap(err(category, str(exc), exc.valid))
    except (AdapterNotConfigured, EdgeAdapterNotConfigured):
        return err("not_configured", f"{name}: adapter not configured on the gateway")
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return err("timeout", f"{name} did not finish in time")
    except (httpx.HTTPError, OSError, ConnectionError) as exc:
        return err("unreachable", f"{name}: {type(exc).__name__}")
    except ValueError as exc:
        return err("invalid_argument", str(exc))
    except Exception as exc:  # noqa: BLE001 - a tool must always answer with an envelope
        return err("unreachable", f"{name}: {type(exc).__name__}")
