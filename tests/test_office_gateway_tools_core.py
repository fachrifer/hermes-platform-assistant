import asyncio
import json

from office_gateway.actions import ActionError
from office_gateway.tools.core import (
    RESULT_CAP,
    Param,
    Tool,
    ToolError,
    call_tool,
    fit_items,
    validate_args,
)


async def _echo(ctx, args, role):
    return {"args": args, "role": role}


async def _boom(ctx, args, role):
    raise ToolError("no_metrics", "empty", valid=["a"])


async def _action_error(ctx, args, role):
    raise ActionError("unknown container", "invalid_argument", ["x", "y"])


async def _huge(ctx, args, role):
    return {"blob": "x" * (RESULT_CAP + 1000)}


async def _slow(ctx, args, role):
    await asyncio.sleep(1)
    return {}


def _tool(tool_name, handler, /, **params):
    return Tool(name=tool_name, roles=frozenset({"obs"}), description=tool_name, params=params, handler=handler)


REG = {
    t.name: t
    for t in (
        _tool(
            "echo",
            _echo,
            window=Param("string", "window", enum=("5m", "1h"), default="5m"),
            lines=Param("integer", "lines", minimum=1, maximum=100, default=30),
            name=Param("string", "name", required=True, max_length=10),
        ),
        _tool("boom", _boom),
        _tool("act", _action_error),
        _tool("huge", _huge),
        _tool("slow", _slow),
    )
}


def _call(name, args=None, role="obs"):
    return asyncio.run(call_tool(None, REG, role, name, args or {}))


def test_ok_envelope_with_defaults():
    out = _call("echo", {"name": "a"})
    assert out == {"ok": True, "data": {"args": {"name": "a", "window": "5m", "lines": 30}, "role": "obs"}}


def test_invalid_arguments():
    assert _call("echo", {})["error"]["category"] == "invalid_argument"
    out = _call("echo", {"name": "a", "window": "2d"})
    assert out["error"]["valid"] == ["5m", "1h"]
    assert _call("echo", {"name": "a", "lines": 500})["error"]["category"] == "invalid_argument"
    assert _call("echo", {"name": "a", "extra": 1})["error"]["category"] == "invalid_argument"
    assert _call("echo", {"name": "x" * 11})["error"]["category"] == "invalid_argument"


def test_validate_args_rejects_non_object():
    try:
        validate_args(REG["echo"], ["name"])
    except ToolError as exc:
        assert exc.category == "invalid_argument"
    else:
        raise AssertionError("expected ToolError")


def test_unknown_tool_and_role_forbidden():
    assert _call("nope")["error"]["category"] == "invalid_argument"
    assert _call("echo", {"name": "a"}, role="vector")["error"]["category"] == "forbidden"


def test_tool_and_action_errors_map_to_categories():
    assert _call("boom")["error"] == {"category": "no_metrics", "detail": "empty", "valid": ["a"]}
    assert _call("act")["error"]["valid"] == ["x", "y"]


def test_results_are_capped():
    out = _call("huge")
    assert len(json.dumps(out)) <= RESULT_CAP
    assert out["ok"] is True and out["data"]["truncated"] is True


def test_timeout(monkeypatch):
    import office_gateway.tools.core as core

    monkeypatch.setattr(core, "TOOL_TIMEOUT_SECONDS", 0.05)
    assert _call("slow")["error"]["category"] == "timeout"


def test_input_schema_shape():
    schema = REG["echo"].input_schema()
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert schema["required"] == ["name"]
    assert schema["properties"]["window"]["enum"] == ["5m", "1h"]


def test_fit_items_respects_budget():
    kept, omitted = fit_items([{"n": "x" * 100} for _ in range(50)], budget=500)
    assert len(kept) < 50 and omitted == 50 - len(kept)
