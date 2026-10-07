import asyncio
import json
from pathlib import Path

import pytest

from gw_helpers import make_config
from office_gateway import grafana_api, grafana_report
from office_gateway.grafana_api import GrafanaClientError
from office_gateway.store import GatewayStore
from office_gateway.tools import REGISTRY
from office_gateway.tools.core import RESULT_CAP, ToolContext, call_tool

GRAFANA_DIR = Path(__file__).resolve().parents[1] / "deploy" / "office-assistant" / "grafana"
DS = {"type": "prometheus", "uid": "${datasource}"}
UP_MAP = [{"type": "value", "options": {
    "-1": {"text": "NO DATA", "color": "yellow"}, "0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}]


def _dash(panels, variables=None):
    return {
        "title": "Test board", "uid": "t1",
        "templating": {"list": variables if variables is not None else [
            {"name": "datasource", "type": "datasource", "query": "prometheus",
             "current": {"text": "prometheus", "value": "promuid"}},
            {"name": "host", "type": "query", "includeAll": True, "allValue": ".*", "current": {"text": ["All"], "value": ["$__all"]}},
        ]},
        "panels": panels,
    }


def _panel(pid, title, ptype="stat", exprs=("up",), defaults=None, **extra):
    return {
        "id": pid, "title": title, "type": ptype, "datasource": DS,
        "fieldConfig": {"defaults": defaults or {}, "overrides": []},
        "targets": [{"expr": e, "refId": chr(65 + i), "legendFormat": extra.pop("legend", "")} for i, e in enumerate(exprs)],
        **extra,
    }


def _series(*values, labels=None):
    return {"series": [{"labels": labels or {}, "values": list(values)}]}


@pytest.fixture
def grafana(monkeypatch):
    """Fake Grafana: dashboards by uid, and a query runner that records everything it was asked."""
    state = {"dashboards": {}, "queries": [], "answer": lambda expr: _series(1.0, 1.0, 1.0, 1.0)}

    def fetch(base, token, uid):
        if uid not in state["dashboards"]:
            raise GrafanaClientError("invalid_argument", "grafana HTTP 404: not found")
        return {"dashboard": state["dashboards"][uid], "url": f"http://grafana.test/d/{uid}", "folder": "General"}

    def run(base, token, ds_uid, ds_type, queries, time_from, time_to, step):
        state["queries"].extend((ds_uid, ds_type, expr) for _, expr in queries)
        return {ref: state["answer"](expr) for ref, expr in queries}

    monkeypatch.setattr(grafana_report, "fetch_dashboard", fetch)
    monkeypatch.setattr(grafana_report, "run_queries", run)
    monkeypatch.setattr(grafana_api, "apply_datasource_defaults", lambda *a, **k: None)
    return state


def _ctx(tmp_path, **cfg):
    config = make_config(tmp_path, grafana_base_url="http://grafana.test", grafana_token="tok", **cfg)
    return ToolContext(config, GatewayStore(config.db_path), None, None, None, None, None, None, None)


def _call(ctx, args=None, role="obs"):
    return asyncio.run(call_tool(ctx, REGISTRY, role, "grafana_report", args or {}))


def _build(uids=("t1",)):
    return grafana_report.build_report("http://g", "tok", list(uids), "now-6h", "now")


# ------------------------------------------------------------------------------ formatting and judging


@pytest.mark.parametrize("value,unit,expected", [
    (96.3, "percent", "96.3%"),
    (0.5, "percentunit", "50%"),
    (868678057, "decbytes", "869 MB"),
    (144099508224, "bytes", "134 GiB"),
    (38287, "short", "38.3K"),
    (4, "short", "4"),
    (0.2, "reqps", "0.2 req/s"),
    (36, "celsius", "36°C"),
    (10982315, "dtdhms", "127d 2h"),
    (None, "percent", "-"),
])
def test_format_value(value, unit, expected):
    assert grafana_report.format_value(value, unit) == expected


def test_value_mappings_beat_thresholds_and_no_data_is_a_warning():
    panel = _panel(1, "Hosts", defaults={"mappings": UP_MAP, "thresholds": {"mode": "absolute", "steps": [
        {"color": "red", "value": None}, {"color": "green", "value": 1}]}})
    assert grafana_report.judge(panel, 1.0, "", None) == ("UP", "ok")
    assert grafana_report.judge(panel, 0.0, "", None) == ("DOWN", "critical")
    assert grafana_report.judge(panel, -1.0, "", None) == ("NO DATA", "warning")


def test_thresholds_follow_the_dashboard_and_percentage_mode_needs_a_max():
    steps = [{"color": "green", "value": None}, {"color": "yellow", "value": 80}, {"color": "red", "value": 90}]
    gauge = _panel(1, "Disk", "gauge", defaults={"unit": "percent", "thresholds": {"mode": "absolute", "steps": steps}})
    assert [grafana_report.judge(gauge, v, "percent", None)[1] for v in (10, 85, 99.8)] == ["ok", "warning", "critical"]
    pct = _panel(2, "MinIO", "gauge", defaults={"max": 100, "thresholds": {"mode": "percentage", "steps": steps}})
    assert grafana_report.judge(pct, 95, "", None)[1] == "critical"
    no_max = _panel(3, "MinIO", "gauge", defaults={"thresholds": {"mode": "percentage", "steps": steps}})
    assert grafana_report.judge(no_max, 95, "", None)[1] == "info"


def test_timeseries_thresholds_only_count_when_the_dashboard_draws_them():
    steps = [{"color": "green", "value": None}, {"color": "red", "value": 80}]
    quiet = _panel(1, "CPU", "timeseries", defaults={"thresholds": {"mode": "absolute", "steps": steps},
                                                      "custom": {"thresholdsStyle": {"mode": "off"}}})
    drawn = _panel(2, "CPU", "timeseries", defaults={"thresholds": {"mode": "absolute", "steps": steps},
                                                      "custom": {"thresholdsStyle": {"mode": "dashed"}}})
    assert grafana_report.judge(quiet, 95, "", None)[1] == "info"
    assert grafana_report.judge(drawn, 95, "", None)[1] == "critical"


# ------------------------------------------------------------------------------ reading dashboards


def test_every_query_of_every_panel_runs_with_variables_bound(grafana):
    grafana["dashboards"]["t1"] = _dash([
        {"type": "row", "id": 100, "title": "Hosts", "panels": []},
        _panel(1, "Hosts up", exprs=['up{instance="a"}', 'up{instance="b"}', 'up{instance="c"}']),
        _panel(2, "CPU", "timeseries", exprs=['rate(x{instance=~"$host"}[$__rate_interval])']),
        {"type": "text", "id": 3, "title": "Notes"},
    ])
    report = _build()
    exprs = [q[2] for q in grafana["queries"]]
    assert len(exprs) == 4, "grafana_panel stops at the first query; the report must not"
    assert 'instance=~".*"' in exprs[3] and "$" not in exprs[3]
    assert {q[0] for q in grafana["queries"]} == {"promuid"}
    panels = {p["title"]: p for p in report["dashboards"][0]["panels"]}
    assert len(panels["Hosts up"]["series"]) == 3 and panels["Hosts up"]["row"] == "Hosts"
    assert panels["Notes"]["state"] == "skipped"


def test_bare_log_queries_are_not_pulled_but_metric_logql_is(grafana):
    loki = {"type": "loki", "uid": "lokiuid"}
    grafana["dashboards"]["t1"] = _dash([
        {**_panel(1, "Lines", "timeseries", exprs=['{app="x"} |= "error"']), "datasource": loki},
        {**_panel(2, "Error rate", "timeseries", exprs=['sum(rate({app="x"} |= "error" [5m]))']), "datasource": loki},
    ])
    report = _build()
    assert [q[2] for q in grafana["queries"]] == ['sum(rate({app="x"} |= "error" [5m]))']
    states = {p["title"]: p["state"] for p in report["dashboards"][0]["panels"]}
    assert states == {"Lines": "skipped", "Error rate": "ok"}


def test_a_failing_query_or_empty_panel_is_reported_not_hidden(grafana):
    grafana["dashboards"]["t1"] = _dash([_panel(1, "Broken", exprs=["bad("]), _panel(2, "Idle", exprs=["idle"]),
                                         _panel(3, "Fine", exprs=["fine"])])
    grafana["answer"] = lambda expr: {"error": "parse error"} if expr == "bad(" else (
        {"series": []} if expr == "idle" else _series(1, 2, 3, 4))
    report = _build()
    states = {p["title"]: p["state"] for p in report["dashboards"][0]["panels"]}
    assert states == {"Broken": "error", "Idle": "no_data", "Fine": "ok"}
    assert report["summary"]["errors"] == 1 and report["summary"]["no_data"] == 1
    reasons = {row["panel"]: row["value"] for row in grafana_report.attention_items(report)}
    assert "parse error" in reasons["Broken"] and reasons["Idle"] == "no data in range"


def test_one_missing_dashboard_does_not_sink_the_report(grafana):
    grafana["dashboards"]["t1"] = _dash([_panel(1, "Fine")])
    report = _build(("t1", "gone"))
    errors = {s["uid"]: s.get("error") for s in report["dashboards"]}
    assert errors["gone"] and not errors["t1"]


def test_all_dashboards_failing_raises_the_grafana_error(grafana):
    with pytest.raises(GrafanaClientError):
        _build(("gone",))


def test_rate_interval_grows_with_the_range(grafana):
    grafana["dashboards"]["t1"] = _dash([_panel(1, "R", exprs=["rate(x[$__rate_interval])"])])
    grafana_report.build_report("http://g", "tok", ["t1"], "now-7d", "now")
    window = grafana["queries"][-1][2]
    assert window != "rate(x[2m])" and "$" not in window


# ------------------------------------------------------------------------------ the shipped dashboards


@pytest.mark.parametrize("name", ["fleet-overview", "milvus-monitor"])
def test_shipped_dashboards_run_every_query_with_no_unbound_variable(grafana, name):
    raw = json.loads((GRAFANA_DIR / f"{name}.json").read_text(encoding="utf-8"))
    grafana["dashboards"]["t1"] = raw
    report = _build()
    panels = [p for p in raw["panels"] if p["type"] != "row"]
    expected = sum(1 for p in panels for t in p["targets"])
    assert len(grafana["queries"]) == expected
    assert all("$" not in q[2] for q in grafana["queries"])
    assert {q[0] for q in grafana["queries"]} == {"afdxlp9p1t14wf"}
    notes = [p["note"] for p in report["dashboards"][0]["panels"] if p["note"]]
    assert not notes, notes


def test_fleet_status_tiles_report_down_and_no_data_from_the_real_dashboard(grafana):
    raw = json.loads((GRAFANA_DIR / "fleet-overview.json").read_text(encoding="utf-8"))
    grafana["dashboards"]["t1"] = raw

    def answer(expr):
        if "10.216.78.129" in expr:
            return _series(-1, -1, -1, -1)
        if "10.216.4.80:9100" in expr and expr.startswith("max(up"):
            return _series(0, 0, 0, 0)
        return _series(1, 1, 1, 1)

    grafana["answer"] = answer
    report = _build()
    hosts = next(p for p in report["dashboards"][0]["panels"] if p["title"] == "Hosts")
    levels = {s["name"]: (s["text"], s["level"]) for s in hosts["series"]}
    assert levels["Rancher 10.216.78.129"] == ("NO DATA", "warning")
    assert levels["Lab-host 10.216.4.80"] == ("DOWN", "critical")
    assert levels["GPU node dc141f0601srv"][1] == "ok"


# ------------------------------------------------------------------------------ the tool


def test_tool_writes_html_and_returns_a_small_summary(grafana, tmp_path, monkeypatch):
    monkeypatch.setattr(grafana_report, "REPORT_DIRS", (str(tmp_path / "reports"),))
    grafana["dashboards"].update({
        "fleet-overview": _dash([_panel(1, "Targets down", defaults={"thresholds": {"mode": "absolute", "steps": [
            {"color": "green", "value": None}, {"color": "red", "value": 1}]}})]),
        grafana_report.DASHBOARDS["milvus"]: _dash([_panel(2, "Query nodes")]),
        grafana_report.DASHBOARDS["insightface"]: _dash([_panel(3, "Faces")]),
    })
    grafana["answer"] = lambda expr: _series(3, 3, 3, 3)
    out = _call(_ctx(tmp_path))
    assert out["ok"] is True, out
    data = out["data"]
    assert data["summary"]["dashboards"] == 3 and data["summary"]["critical"] == 1
    assert data["attention"][0]["panel"] == "Targets down" and data["attention"][0]["level"] == "critical"
    assert len(json.dumps(out, ensure_ascii=False)) <= RESULT_CAP
    saved = tmp_path / "reports" / data["report"]["file"]
    assert saved.exists() and (tmp_path / "reports" / data["report"]["latest"]).exists()
    page = saved.read_text(encoding="utf-8")
    assert "Targets down" in page and "CRITICAL" in page and "Test board" in page
    assert "vm_path" not in data["report"], "only /reports is the shared VM folder"


def test_tool_reports_to_the_shared_reports_folder_path_on_the_vm(grafana, tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, so nothing can be created below it")
    monkeypatch.setattr(grafana_report, "REPORT_DIRS", (str(blocker / "sub"), str(shared)))
    grafana["dashboards"]["fleet-overview"] = _dash([_panel(1, "A")])
    data = _call(_ctx(tmp_path), {"scope": "fleet"})["data"]
    assert (shared / data["report"]["file"]).exists(), "falls back to the next writable folder"


def test_tool_takes_a_scope_a_uid_and_a_time_range(grafana, tmp_path, monkeypatch):
    monkeypatch.setattr(grafana_report, "REPORT_DIRS", (str(tmp_path / "r"),))
    grafana["dashboards"]["custom1"] = _dash([_panel(1, "A")])
    ctx = _ctx(tmp_path)
    assert _call(ctx, {"uid": "custom1", "time_range": "now-24h"})["data"]["range"] == "now-24h to now"
    assert _call(ctx, {"scope": "nope"})["error"]["category"] == "invalid_argument"
    assert _call(ctx, {"time_range": "yesterday"})["error"]["category"] == "invalid_argument"


def test_a_huge_dashboard_still_fits_the_tool_result_cap(grafana, tmp_path, monkeypatch):
    monkeypatch.setattr(grafana_report, "REPORT_DIRS", (str(tmp_path / "r"),))
    red = {"thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}}
    grafana["dashboards"]["fleet-overview"] = _dash(
        [_panel(i, f"Very long panel title number {i} " + "x" * 60, defaults=red) for i in range(1, 150)])
    grafana["answer"] = lambda expr: _series(5, 5, 5, 5)
    out = _call(_ctx(tmp_path), {"scope": "fleet"})
    assert out["ok"] is True and "truncated" not in out["data"], "must not fall back to a cut-off blob"
    assert out["data"]["attention_omitted"] > 0 and out["data"]["summary"]["critical"] == grafana_report.MAX_QUERIES_PER_DASHBOARD


def test_tool_is_obs_only_and_needs_grafana(grafana, tmp_path):
    ctx = _ctx(tmp_path)
    assert _call(ctx, role="llm")["error"]["category"] == "forbidden"
    assert _call(ctx, role="supervisor")["error"]["category"] == "forbidden"
    bare = ToolContext(make_config(tmp_path), GatewayStore(make_config(tmp_path).db_path), None, None, None, None, None, None, None)
    assert _call(bare)["error"]["category"] == "not_configured"


def test_html_escapes_panel_titles(grafana, tmp_path):
    grafana["dashboards"]["t1"] = _dash([_panel(1, "<script>alert(1)</script>")])
    page = grafana_report.render_html(_build())
    assert "<script>alert" not in page and "&lt;script&gt;" in page
