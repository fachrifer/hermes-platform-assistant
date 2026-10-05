import asyncio
import json

from office_gateway import grafana_api
from office_gateway.actions import ActionService
from office_gateway.mcp_requests import ARCHIVE_DASHBOARD_UIDS, archive_dashboard_spec
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
    assert out["data"]["omitted"] == 0


def test_grafana_dashboards_returns_every_dashboard(tmp_path, monkeypatch):
    rows = [
        {
            "title": f"Dashboard {index}",
            "uid": f"d{index}",
            "folder": "General",
            "url": f"http://grafana.test/d/d{index}/dashboard-{index}",
        }
        for index in range(17)
    ]
    monkeypatch.setattr(grafana_api, "search_dashboards", lambda base, token: rows)
    ctx, _ = _ctx(tmp_path)
    out = asyncio.run(call_tool(ctx, REGISTRY, "obs", "grafana_dashboards", {}))
    assert out["ok"] is True
    assert out["data"]["count"] == 17
    assert len(out["data"]["dashboards"]) == 17
    assert out["data"]["omitted"] == 0
    assert "truncated" not in out["data"]


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


def test_grafana_dashboard_fits_a_full_board(tmp_path, monkeypatch):
    panels = [
        {
            "id": index,
            "title": f"Panel {index} node resource",
            "type": "timeseries",
            "row": "Nodes",
            "queries": ["avg(rate(container_cpu_usage_seconds_total[5m])) " + ("by (pod) " * 8)],
        }
        for index in range(35)
    ]
    monkeypatch.setattr(
        grafana_api,
        "get_dashboard",
        lambda base, token, uid: {
            "title": "K8S Dashboard",
            "uid": uid,
            "folder": "General",
            "url": "http://grafana.test/d/" + uid,
            "panels": panels,
        },
    )
    ctx, _ = _ctx(tmp_path)
    out = asyncio.run(call_tool(ctx, REGISTRY, "obs", "grafana_dashboard", {"uid": "k8s"}))
    assert out["ok"] is True and out["data"]["count"] == 35 and out["data"]["omitted"] == 0


def test_grafana_dashboard_flattens_rows_and_keeps_the_first_query(tmp_path, monkeypatch):
    monkeypatch.setattr(
        grafana_api,
        "get_dashboard",
        lambda base, token, uid: {
            "title": "K8S Dashboard",
            "uid": uid,
            "folder": "General",
            "url": "http://grafana.test/d/" + uid,
            "panels": [
                {
                    "id": 54,
                    "title": "Overview",
                    "type": "row",
                    "row": "",
                    "queries": [],
                },
                {
                    "id": 44,
                    "title": "Node Memory Ratio",
                    "type": "bargauge",
                    "row": "Overview",
                    "queries": ["node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes", "second"],
                },
            ],
        },
    )
    ctx, _ = _ctx(tmp_path)
    out = asyncio.run(call_tool(ctx, REGISTRY, "obs", "grafana_dashboard", {"uid": "k8s"}))
    assert out["ok"] is True
    data = out["data"]
    assert data["count"] == 2 and data["omitted"] == 0
    assert data["panels"][1]["query"].startswith("node_memory")
    assert data["panels"][1]["queries"] == 2


def test_grafana_panel_runs_the_stored_query(tmp_path, monkeypatch):
    seen = []

    async def query(url, expr):
        seen.append(expr)
        return {"data": {"result": [{"metric": {"instance": "10.216.4.80"}, "value": [0, "0.42"]}]}}

    monkeypatch.setattr(
        grafana_api,
        "get_dashboard",
        lambda base, token, uid: {
            "title": "K8S",
            "uid": uid,
            "folder": "General",
            "url": "http://grafana.test/d/" + uid,
            "panels": [
                {"id": 44, "title": "Memory", "type": "bargauge", "queries": ["up{job=\"node\"}", "other"]},
                {"id": 45, "title": "Picked node", "type": "stat", "queries": ["up{instance=\"$Node\"}"]},
            ],
        },
    )
    monkeypatch.setattr("office_gateway.tools.obs.instant_query", query)
    ctx, _ = _ctx(tmp_path, metrics_url="http://vm.test")
    out = asyncio.run(call_tool(ctx, REGISTRY, "obs", "grafana_panel", {"uid": "k8s", "panel_id": 44}))
    assert out["ok"] is True
    assert seen == ["up{job=\"node\"}"]
    assert out["data"]["values"] == [{"labels": {"instance": "10.216.4.80"}, "value": 0.42}]
    assert out["data"]["more_queries"] == 1
    skipped = asyncio.run(call_tool(ctx, REGISTRY, "obs", "grafana_panel", {"uid": "k8s", "panel_id": 45}))
    assert skipped["ok"] is True and skipped["data"]["templated"] == 1 and skipped["data"]["values"] == []


def test_grafana_panel_schema_accepts_variable_binding():
    schema = REGISTRY["grafana_panel"].input_schema()
    assert schema["properties"]["vars"]["type"] == "object"
    assert schema["properties"]["time_range"]["type"] == "string"
    assert "uid" in schema["required"] and "panel_id" in schema["required"]
    assert "vars" not in schema["required"]


def test_grafana_panel_binds_vars_and_queries_the_datasource(tmp_path, monkeypatch):
    seen = []

    def query(base, token, datasource_uid, expr, time_from, time_to):
        seen.append((datasource_uid, expr, time_from, time_to))
        return {"data": {"result": [{"metric": {"instance": "10.216.4.80", "namespace": "milvus"}, "value": [0, "3.5"]}]}}

    monkeypatch.setattr(
        grafana_api,
        "get_dashboard",
        lambda base, token, uid: {
            "title": "K8S Dashboard",
            "uid": uid,
            "folder": "General",
            "url": "http://grafana.test/d/" + uid,
            "variables": [
                {"name": "Node", "current": "", "all_value": "", "include_all": True},
                {"name": "NameSpace", "current": "milvus", "all_value": "", "include_all": True},
                {"name": "origin_prometheus", "current": "prom-default", "all_value": "", "include_all": False},
            ],
            "panels": [
                {
                    "id": 12,
                    "title": "Pod CPU",
                    "type": "timeseries",
                    "datasource": "${origin_prometheus}",
                    "queries": ['rate(container_cpu_usage_seconds_total{node=~"$Node",namespace=~"$NameSpace"}[2m])'],
                }
            ],
        },
    )
    monkeypatch.setattr(grafana_api, "query_datasource", query)
    ctx, _ = _ctx(tmp_path)
    out = asyncio.run(
        call_tool(
            ctx,
            REGISTRY,
            "obs",
            "grafana_panel",
            {
                "uid": "k8s",
                "panel_id": 12,
                "vars": {"origin_prometheus": "prom1", "Node": ".*", "NameSpace": ".*"},
                "time_range": "now-1h",
            },
        )
    )
    assert out["ok"] is True
    assert seen == [
        (
            "prom1",
            'rate(container_cpu_usage_seconds_total{node=~".*",namespace=~".*"}[2m])',
            "now-1h",
            "now",
        )
    ]
    assert out["data"]["values"] == [{"labels": {"instance": "10.216.4.80", "namespace": "milvus"}, "value": 3.5}]
    assert out["data"]["templated"] == 0


def test_grafana_panel_uses_templating_defaults_for_omitted_vars(tmp_path, monkeypatch):
    seen = []

    def query(base, token, datasource_uid, expr, time_from, time_to):
        seen.append((datasource_uid, expr, time_from, time_to))
        return {"data": {"result": [{"metric": {"pod": "milvus-0"}, "value": [0, "1"]}]}}

    monkeypatch.setattr(
        grafana_api,
        "get_dashboard",
        lambda base, token, uid: {
            "title": "K8S Dashboard",
            "uid": uid,
            "folder": "General",
            "url": "http://grafana.test/d/" + uid,
            "variables": [
                {"name": "Pod", "current": "milvus-.*", "all_value": ".*", "include_all": True},
                {"name": "origin_prometheus", "current": "prom1", "all_value": "", "include_all": False},
            ],
            "panels": [
                {
                    "id": 8,
                    "title": "Pod up",
                    "type": "stat",
                    "datasource": "$origin_prometheus",
                    "queries": ['up{pod=~"$Pod"}'],
                }
            ],
        },
    )
    monkeypatch.setattr(grafana_api, "query_datasource", query)
    ctx, _ = _ctx(tmp_path)
    out = asyncio.run(call_tool(ctx, REGISTRY, "obs", "grafana_panel", {"uid": "k8s", "panel_id": 8, "time_range": "now-6h,now"}))
    assert out["ok"] is True
    assert seen == [("prom1", 'up{pod=~"milvus-.*"}', "now-6h", "now")]


def test_summarize_panels_reads_queries_nested_under_a_row():
    panels = grafana_api.summarize_panels(
        {
            "panels": [
                {
                    "id": 1,
                    "type": "row",
                    "title": "Nodes",
                    "panels": [
                        {"id": 2, "type": "stat", "title": "Up", "targets": [{"expr": "up"}, {"refId": "B"}]},
                    ],
                }
            ]
        }
    )
    assert panels == [{"id": 2, "title": "Up", "type": "stat", "queries": ["up"], "row": "Nodes"}]


def test_propose_mcp_change_files_the_archive_spec_without_touching_grafana(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("grafana must stay unchanged")

    monkeypatch.setattr(grafana_api, "create_dashboard", forbidden)
    monkeypatch.setattr(grafana_api, "search_dashboards", forbidden)
    monkeypatch.setattr(grafana_api, "query_datasource", forbidden)
    monkeypatch.setattr(ActionService, "_restart_gateway", lambda self: None)
    ctx, actions = _ctx(tmp_path)
    spec = json.dumps(archive_dashboard_spec(), indent=2) + "\nwhpg_note"
    source = (
        "from office_gateway.tools.core import Param, Tool\n"
        "async def _whpg(ctx, args, role):\n"
        "    return {'uid': args['uid']}\n"
        "TOOLS = (Tool('whpg_note', frozenset({'obs'}), 'Note one dashboard.', "
        "{'uid': Param('string', 'uid', required=True, max_length=64)}, _whpg),)\n"
    )
    out = asyncio.run(
        call_tool(
            ctx,
            REGISTRY,
            "supervisor",
            "propose_mcp_change",
            {
                "name": "whpg_note",
                "summary": "Add whpg_note",
                "spec": spec,
                "source": source,
                "reason": "7 empty WHPG dashboards in General",
            },
        )
    )
    assert out["ok"] is True and out["data"]["status"] == "pending"
    pending = actions.status("supervisor", out["data"]["action_id"])
    assert pending.action == "add_mcp_tool"
    assert pending.target == "whpg_note"
    diff = "\n".join(pending.params["diff"])
    assert "_archived" in diff
    assert "hard_delete" in spec
    for row in ARCHIVE_DASHBOARD_UIDS:
        assert row["uid"] in spec
    again = asyncio.run(
        call_tool(
            ctx,
            REGISTRY,
            "supervisor",
            "propose_mcp_change",
            {
                "name": "whpg_note",
                "summary": "Add whpg_note",
                "spec": spec,
                "source": source,
                "reason": "again",
            },
        )
    )
    assert again["ok"] is False and again["error"]["category"] == "invalid_argument"
    done = actions.approve(out["data"]["action_id"], "timai")
    assert done.status == "succeeded"
    assert done.result["implemented"] is True
    assert (tmp_path / "mcp_plugins" / "whpg_note.py").is_file()


def test_propose_archive_dashboard_moves_only_after_approval(tmp_path, monkeypatch):
    moved = []

    monkeypatch.setattr(
        grafana_api,
        "dashboard_record",
        lambda base, token, uid: {"uid": uid, "title": "Cluster Dashboard", "folder": "General", "dashboard": {"uid": uid}},
    )

    def archive(base, token, *, uid, reason, hard_delete):
        moved.append((uid, reason, hard_delete))
        return {"uid": uid, "title": "Cluster Dashboard", "folder": "_archived", "deleted": hard_delete}

    monkeypatch.setattr(grafana_api, "archive_dashboard", archive)
    ctx, actions = _ctx(tmp_path)
    out = asyncio.run(
        call_tool(
            ctx,
            REGISTRY,
            "obs",
            "propose_archive_dashboard",
            {"uid": "cluster_dashboard_edb", "reason": "dead import"},
        )
    )
    assert out["ok"] is True and out["data"]["status"] == "pending"
    assert moved == []
    pending = actions.status("obs", out["data"]["action_id"])
    assert pending.params["hard_delete"] is False
    assert "General" in "\n".join(pending.params["diff"])
    done = actions.approve(out["data"]["action_id"], "timai")
    assert done.status == "succeeded"
    assert moved == [("cluster_dashboard_edb", "dead import", False)]
    assert done.result["folder"] == "_archived"
    assert done.result["deleted"] is False


def test_archive_save_retries_when_grafana_folder_acl_is_not_ready(monkeypatch):
    calls = {"save": 0, "acl": 0}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def request(client, method, url, token, body=None):
        if url.endswith("/permissions"):
            calls["acl"] += 1
            return {}
        if method == "POST" and url == "/api/dashboards/db":
            calls["save"] += 1
            if calls["save"] == 1:
                raise grafana_api.GrafanaClientError("invalid_argument", 'grafana HTTP 500: {"message":"Failed to save dashboard"}')
            return {"status": "success"}
        raise AssertionError(url)

    monkeypatch.setattr(grafana_api, "_client", lambda base, token: _Client())
    monkeypatch.setattr(grafana_api, "_request", request)
    monkeypatch.setattr(grafana_api, "_read_dashboard", lambda client, token, uid: {"title": "Cluster", "uid": uid, "folder": "General", "dashboard": {"uid": uid}})
    monkeypatch.setattr(grafana_api, "_ensure_folder", lambda client, token, title: "folder1")
    monkeypatch.setattr(grafana_api.time, "sleep", lambda seconds: None)
    result = grafana_api.archive_dashboard("http://grafana.test", "tok", uid="cluster_dashboard_edb", reason="dead import")
    assert result["folder"] == "_archived" and result["deleted"] is False
    assert calls["acl"] == 1 and calls["save"] == 2


def test_archive_failure_keeps_the_grafana_message(tmp_path, monkeypatch):
    def boom(base, token, *, uid, reason, hard_delete):
        raise grafana_api.GrafanaClientError("invalid_argument", 'grafana HTTP 500: {"message":"Failed to save dashboard"}')

    monkeypatch.setattr(grafana_api, "archive_dashboard", boom)
    ctx, actions = _ctx(tmp_path)
    monkeypatch.setattr(
        grafana_api,
        "dashboard_record",
        lambda base, token, uid: {"uid": uid, "title": "Cluster Dashboard", "folder": "General", "dashboard": {"uid": uid}},
    )
    out = asyncio.run(
        call_tool(ctx, REGISTRY, "obs", "propose_archive_dashboard", {"uid": "cluster_dashboard_edb", "reason": "dead import"})
    )
    done = actions.approve(out["data"]["action_id"], "timai")
    assert done.status == "failed"
    assert done.detail == "failed:invalid"
    assert "Failed to save dashboard" in done.result["detail"]


def test_propose_archive_dashboard_rejects_a_missing_uid(tmp_path, monkeypatch):
    def missing(base, token, uid):
        raise grafana_api.GrafanaClientError("not_found", f"dashboard {uid} was not found")

    monkeypatch.setattr(grafana_api, "dashboard_record", missing)
    ctx, _ = _ctx(tmp_path)
    out = asyncio.run(
        call_tool(
            ctx,
            REGISTRY,
            "obs",
            "propose_archive_dashboard",
            {"uid": "missing", "reason": "gone"},
        )
    )
    assert out["ok"] is False and out["error"]["category"] == "not_found"


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
