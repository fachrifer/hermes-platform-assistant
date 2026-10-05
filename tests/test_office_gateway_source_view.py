import asyncio
from pathlib import Path

from office_gateway.actions import ActionService
from office_gateway.mcp_plugins import active_registry
from office_gateway.source_view import read_source
from office_gateway.store import GatewayStore
from office_gateway.tools import REGISTRY
from office_gateway.tools.core import ToolContext, call_tool
from gw_helpers import make_config

REPLACEMENT = """
from office_gateway.tools.core import Tool

async def _models(ctx, args, role):
    return {"models": ["ranked"]}

TOOLS = (
    Tool("list_models", frozenset({"llm"}), "Ranked model list.", {}, _models),
)
"""


def test_propose_script_writes_only_after_approval(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    monkeypatch.setenv("OFFICE_GATEWAY_SCRIPTS", str(scripts))
    config = make_config(tmp_path, console_url="https://console.test")
    actions = ActionService(config, GatewayStore(config.db_path))
    ctx = ToolContext(config, actions.store, actions, None, None, None, None, None, None)
    out = asyncio.run(
        call_tool(
            ctx,
            REGISTRY,
            "supervisor",
            "propose_script",
            {"path": "scripts/rank_models.sh", "content": "echo rank\n", "reason": "ranking"},
        )
    )
    assert out["ok"] is True and out["data"]["status"] == "pending"
    assert not (scripts / "rank_models.sh").exists()
    done = actions.approve(out["data"]["action_id"], "timai")
    assert done.status == "succeeded"
    assert (scripts / "rank_models.sh").read_text(encoding="utf-8") == "echo rank\n"


def test_read_source_rejects_parent_paths_and_env_files(tmp_path):
    root = tmp_path / "office_gateway"
    root.mkdir()
    (root / "tools.py").write_text("print('ok')\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    roots = {"office_gateway": root, "scripts": tmp_path / "scripts"}
    assert read_source(roots, "office_gateway/tools.py")["text"].startswith("print")
    for path in ("../office_gateway/tools.py", "office_gateway/.env", "secrets/tool.py"):
        try:
            read_source(roots, path)
        except ValueError:
            continue
        raise AssertionError(path)


def test_replace_ships_over_a_builtin_and_restart_waits_for_approval(tmp_path, monkeypatch):
    restarted = []
    monkeypatch.setattr(ActionService, "_restart_gateway", lambda self: restarted.append(self))
    config = make_config(tmp_path, console_url="https://console.test")
    actions = ActionService(config, GatewayStore(config.db_path))
    ctx = ToolContext(config, actions.store, actions, None, None, None, None, None, None)
    out = asyncio.run(
        call_tool(
            ctx,
            REGISTRY,
            "supervisor",
            "propose_mcp_change",
            {
                "name": "list_models",
                "summary": "Rank models",
                "spec": "list_models returns a ranked model list",
                "source": REPLACEMENT,
                "replace": True,
                "reason": "ranking",
            },
        )
    )
    assert out["ok"] is True
    assert restarted == []
    done = actions.approve(out["data"]["action_id"], "timai")
    assert done.result["implemented"] is True
    assert active_registry(config)["list_models"].description == "Ranked model list."
    pending = asyncio.run(
        call_tool(ctx, REGISTRY, "supervisor", "propose_gateway_restart", {"reason": "reload"})
    )
    assert pending["ok"] is True and pending["data"]["status"] == "pending"
    assert len(restarted) == 1
    actions.approve(pending["data"]["action_id"], "timai")
    assert len(restarted) == 2
