import asyncio

from office_gateway.actions import ActionService
from office_gateway.mcp_plugins import active_registry, load_plugins, plugin_dir
from office_gateway.store import GatewayStore
from office_gateway.tools import REGISTRY
from office_gateway.tools.core import ToolContext, call_tool
from gw_helpers import make_config

READ_SOURCE = """
from office_gateway.tools.core import Param, Tool

async def _echo(ctx, args, role):
    return {"heard": args["text"], "role": role}

TOOLS = (
    Tool(
        "echo_text",
        frozenset({"vector", "obs"}),
        "Echo text for any caller that has the tool.",
        {"text": Param("string", "text", required=True, max_length=100)},
        _echo,
    ),
)
"""

WRITE_SOURCE = """
from office_gateway.tools.common import pending_view
from office_gateway.tools.core import Param, Tool

async def _save(ctx, args, role):
    action = await __import__("asyncio").to_thread(
        ctx.actions.propose,
        role,
        "save_note",
        "note",
        {"text": args["text"], "reason": args["reason"]},
    )
    return pending_view(action, ctx.config.console_url)

TOOLS = (
    Tool(
        "save_note",
        frozenset({"lab-host"}),
        "Save a note after approval.",
        {
            "text": Param("string", "text", required=True, max_length=100),
            "reason": Param("string", "why", required=True, max_length=200),
        },
        _save,
    ),
)

def propose_note(service, role, target, params, reason):
    return service.store.propose(
        action="save_note",
        target=target,
        role=role,
        params={"text": str(params.get("text") or ""), "reason": reason, "diff": ["+ note"]},
        summary="save note",
    )

def execute_note(service, claimed):
    return service._finish(claimed, True, "ok", {"saved": claimed.params.get("text")})

ACTIONS = {"save_note": {"role": "lab-host", "propose": propose_note, "execute": execute_note}}
"""


def _ctx(tmp_path):
    config = make_config(tmp_path, console_url="https://console.test")
    actions = ActionService(config, GatewayStore(config.db_path))
    return ToolContext(config, actions.store, actions, None, None, None, None, None, None), actions


def _propose(ctx, name, source, role="supervisor"):
    return asyncio.run(
        call_tool(
            ctx,
            REGISTRY,
            role,
            "propose_mcp_change",
            {
                "name": name,
                "summary": f"Add {name}",
                "spec": f"{name} does the requested job",
                "source": source,
                "reason": "needed",
            },
        )
    )


def test_read_plugin_is_callable_for_its_roles_after_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(ActionService, "_restart_gateway", lambda self: None)
    ctx, actions = _ctx(tmp_path)
    out = _propose(ctx, "echo_text", READ_SOURCE)
    assert out["ok"] is True
    done = actions.approve(out["data"]["action_id"], "timai")
    assert done.result["implemented"] is True
    registry = active_registry(ctx.config)
    heard = asyncio.run(call_tool(ctx, registry, "vector", "echo_text", {"text": "hello"}))
    assert heard["ok"] is True and heard["data"]["heard"] == "hello"
    denied = asyncio.run(call_tool(ctx, registry, "ingress", "echo_text", {"text": "hello"}))
    assert denied["ok"] is False and denied["error"]["category"] == "forbidden"


def test_write_plugin_runs_only_after_its_own_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(ActionService, "_restart_gateway", lambda self: None)
    ctx, actions = _ctx(tmp_path)
    shipped = _propose(ctx, "save_note", WRITE_SOURCE)
    assert actions.approve(shipped["data"]["action_id"], "timai").result["implemented"] is True
    registry = active_registry(ctx.config)
    pending = asyncio.run(
        call_tool(ctx, registry, "lab-host", "save_note", {"text": "keep", "reason": "record"})
    )
    assert pending["ok"] is True and pending["data"]["status"] == "pending"
    assert actions.status("lab-host", pending["data"]["action_id"]).result == {}
    done = actions.approve(pending["data"]["action_id"], "timai")
    assert done.status == "succeeded" and done.result["saved"] == "keep"


def test_plugin_source_must_be_python_and_must_not_replace_a_builtin(tmp_path, monkeypatch):
    monkeypatch.setattr(ActionService, "_restart_gateway", lambda self: None)
    ctx, _ = _ctx(tmp_path)
    broken = _propose(ctx, "echo_text", "TOOLS = ()\n")
    assert broken["ok"] is False and broken["error"]["category"] == "invalid_argument"
    clash = _propose(ctx, "fleet_status", READ_SOURCE.replace("echo_text", "fleet_status"))
    assert clash["ok"] is False and "cannot be replaced" in clash["error"]["detail"]


def test_a_broken_plugin_file_does_not_hide_builtin_tools(tmp_path):
    directory = plugin_dir(make_config(tmp_path))
    directory.mkdir()
    (directory / "broken_tool.py").write_text("raise RuntimeError('nope')\n", encoding="utf-8")
    loaded = load_plugins(directory, set(REGISTRY))
    assert loaded.tools == ()
    registry = active_registry(make_config(tmp_path))
    assert "fleet_status" in registry
