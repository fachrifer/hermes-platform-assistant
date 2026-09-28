import asyncio
import json

from office_gateway import grafana_api
from office_gateway.actions import ActionService
from office_gateway.grafana_api import render_dashboard
from office_gateway.store import GatewayStore
from office_gateway.tools import REGISTRY
from office_gateway.tools.core import ToolContext, call_tool
from gw_helpers import make_config


def _ctx(tmp_path, **cfg):
    config = make_config(tmp_path, grafana_base_url="http://grafana.test", grafana_token="tok", **cfg)
    store = GatewayStore(config.db_path)
    actions = ActionService(config, store)
    return ToolContext(config, store, actions, None, None, None, None, None, None), actions


def _propose(ctx):
    return asyncio.run(
        call_tool(
            ctx,
            REGISTRY,
            "obs",
            "propose_dashboard",
            {
                "title": "Lab CPU",
                "panels": json.dumps([{"type": "timeseries", "query": "cpu_busy_pct", "instance": "10.216.4.80"}]),
                "reason": "lab cpu",
            },
        )
    )


def test_render_dashboard_uses_the_named_query():
    dashboard = render_dashboard(
        "Lab CPU",
        [{"type": "timeseries", "query": "cpu_busy_pct", "title": "CPU", "instance": "10.216.4.80"}],
        "prom",
    )
    assert dashboard["title"] == "Lab CPU"
    assert "10\\\\.216\\\\.4\\\\.80" in dashboard["panels"][0]["targets"][0]["expr"]
    assert dashboard["panels"][0]["datasource"]["uid"] == "prom"


def test_grafana_dashboards_lists_everything_the_account_can_see(tmp_path, monkeypatch):
    monkeypatch.setattr(
        grafana_api,
        "search_dashboards",
        lambda base, token: [
            {"title": "Node", "uid": "n1", "folder": "General", "url": "http://grafana.test/d/n1"},
            {"title": "GPU", "uid": "g1", "folder": "Hermes Fleet", "url": "http://grafana.test/d/g1"},
        ],
    )
    ctx, _ = _ctx(tmp_path, grafana_dashboards={"pinned-only": "abc"})
    out = asyncio.run(call_tool(ctx, REGISTRY, "obs", "grafana_dashboards", {}))
    assert out["ok"] is True
    assert {row["title"] for row in out["data"]["dashboards"]} == {"Node", "GPU"}


def test_propose_dashboard_waits_for_approval(tmp_path, monkeypatch):
    created = []

    def create(base, token, **kwargs):
        created.append(kwargs["title"])
        return {"title": kwargs["title"], "uid": "hf1", "url": "http://grafana.test/d/hf1", "folder": "General"}

    monkeypatch.setattr(grafana_api, "search_dashboards", lambda base, token: [])
    monkeypatch.setattr(grafana_api, "create_dashboard", create)
    ctx, actions = _ctx(tmp_path)
    out = _propose(ctx)
    assert out["ok"] is True and out["data"]["status"] == "pending"
    assert created == []
    done = actions.approve(out["data"]["action_id"], "timai")
    assert done.status == "succeeded" and created == ["Lab CPU"]
    assert done.result["url"] == "http://grafana.test/d/hf1"


def test_propose_dashboard_suffixes_an_existing_title(tmp_path, monkeypatch):
    monkeypatch.setattr(
        grafana_api,
        "search_dashboards",
        lambda base, token: [{"title": "Lab CPU", "uid": "old", "folder": "General", "url": ""}],
    )
    ctx, actions = _ctx(tmp_path)
    out = _propose(ctx)
    assert out["ok"] is True
    pending = actions.status("obs", out["data"]["action_id"])
    assert pending.target.startswith("Lab CPU ")
    assert pending.target != "Lab CPU"


def test_propose_dashboard_rejects_an_unknown_query(tmp_path, monkeypatch):
    monkeypatch.setattr(grafana_api, "search_dashboards", lambda base, token: [])
    ctx, _ = _ctx(tmp_path)
    out = asyncio.run(
        call_tool(
            ctx,
            REGISTRY,
            "obs",
            "propose_dashboard",
            {"title": "Nope", "panels": json.dumps([{"type": "stat", "query": "drop_table"}]), "reason": "no"},
        )
    )
    assert out["ok"] is False and out["error"]["category"] == "invalid_argument"


def test_other_roles_cannot_propose_a_dashboard(tmp_path, monkeypatch):
    monkeypatch.setattr(grafana_api, "search_dashboards", lambda base, token: [])
    ctx, _ = _ctx(tmp_path)
    out = asyncio.run(
        call_tool(
            ctx,
            REGISTRY,
            "vector",
            "propose_dashboard",
            {"title": "Lab CPU", "panels": "[]", "reason": "no"},
        )
    )
    assert out["ok"] is False and out["error"]["category"] == "forbidden"
