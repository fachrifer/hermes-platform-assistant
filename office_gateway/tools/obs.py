from __future__ import annotations

import asyncio
import json

from office_gateway import grafana_api
from office_gateway.grafana_api import GrafanaClientError
from office_gateway.grafana_links import build_links
from office_gateway.metrics import instant_query
from office_gateway.reports import queries
from office_gateway.tools.common import pending_view
from office_gateway.tools.core import Param, Tool, ToolError, fit_items

OBS = frozenset({"obs"})
_KEEP_LABELS = ("instance", "job", "mountpoint", "device", "service", "namespace", "pod")


async def _metrics_query(ctx, args, role):
    if not ctx.config.metrics_url:
        raise ToolError("not_configured", "OFFICE_METRICS_URL is not set")
    try:
        promql = queries.render(args["name"], args.get("instance"), args.get("window"))
    except ValueError as exc:
        raise ToolError("invalid_argument", str(exc)) from exc
    try:
        payload = await instant_query(ctx.config.metrics_url, promql)
    except json.JSONDecodeError as exc:
        raise ToolError("unreachable", "metrics upstream returned invalid JSON") from exc
    result = (payload.get("data") or {}).get("result") or []
    if not result:
        raise ToolError("no_metrics", f"{args['name']} returned no series")
    rows = []
    for series in result:
        labels = {k: v for k, v in (series.get("metric") or {}).items() if k in _KEEP_LABELS}
        value = series.get("value", [None, None])[1]
        try:
            value = round(float(value), 2)
        except (TypeError, ValueError):
            pass
        rows.append({"labels": labels, "value": value})
    kept, omitted = fit_items(rows, budget=1500)
    return {"query": args["name"], "series": len(rows), "values": kept, "omitted": omitted}


def _preview(panel: dict) -> dict:
    exprs = panel.get("queries") or []
    item = {
        "id": panel["id"],
        "title": panel["title"],
        "type": panel["type"],
        "queries": len(exprs),
    }
    if panel.get("row"):
        item["row"] = panel["row"]
    if exprs:
        item["query"] = exprs[0][:160]
    return item


def _templated(expr: str) -> bool:
    return "$" in expr or "[[" in expr


async def _load_dashboard(ctx, uid: str) -> dict:
    try:
        return await asyncio.to_thread(
            grafana_api.get_dashboard, ctx.config.grafana_base_url, ctx.config.grafana_token, uid
        )
    except GrafanaClientError as exc:
        raise ToolError(exc.category, exc.detail) from exc


async def _grafana_dashboards(ctx, args, role):
    try:
        rows = await asyncio.to_thread(
            grafana_api.search_dashboards, ctx.config.grafana_base_url, ctx.config.grafana_token
        )
    except GrafanaClientError as exc:
        raise ToolError(exc.category, exc.detail) from exc
    return {"count": len(rows), "dashboards": rows, "omitted": 0}


async def _grafana_dashboard(ctx, args, role):
    dashboard = await _load_dashboard(ctx, args["uid"])
    previews = [_preview(panel) for panel in dashboard["panels"]]
    kept, omitted = fit_items(previews, budget=12000)
    return {
        "title": dashboard["title"],
        "uid": dashboard["uid"],
        "folder": dashboard["folder"],
        "url": dashboard["url"],
        "count": len(previews),
        "panels": kept,
        "omitted": omitted,
    }


async def _grafana_panel(ctx, args, role):
    try:
        time_from, time_to = grafana_api.parse_time_range(args.get("time_range"))
    except ValueError as exc:
        raise ToolError("invalid_argument", str(exc)) from exc
    dashboard = await _load_dashboard(ctx, args["uid"])
    panel = next((item for item in dashboard["panels"] if item["id"] == args["panel_id"]), None)
    if panel is None:
        raise ToolError("invalid_argument", f"panel {args['panel_id']} is not on this dashboard")
    bound = grafana_api.bind_variables(dashboard.get("variables") or [], args.get("vars"), time_from)
    concrete = [grafana_api.substitute(expr, bound) for expr in panel["queries"]]
    concrete = [expr for expr in concrete if not _templated(expr)]
    templated = len(panel["queries"]) - len(concrete)
    view = {
        "id": panel["id"],
        "title": panel["title"],
        "type": panel["type"],
        "queries": len(panel["queries"]),
        "templated": templated,
    }
    if not concrete:
        view["query"] = (panel["queries"][0][:160] if panel["queries"] else "")
        view["values"] = []
        view["omitted"] = 0
        return view
    datasource = grafana_api.substitute(str(panel.get("datasource") or ""), bound)
    if _templated(datasource):
        datasource = bound.get("origin_prometheus") or ""
    try:
        if datasource and not _templated(datasource):
            payload = await asyncio.to_thread(
                grafana_api.query_datasource,
                ctx.config.grafana_base_url,
                ctx.config.grafana_token,
                datasource,
                concrete[0],
                time_from,
                time_to,
            )
        else:
            if not ctx.config.metrics_url:
                raise ToolError("not_configured", "OFFICE_METRICS_URL is not set")
            payload = await instant_query(ctx.config.metrics_url, concrete[0])
    except GrafanaClientError as exc:
        raise ToolError(exc.category, exc.detail) from exc
    except json.JSONDecodeError as exc:
        raise ToolError("unreachable", "metrics upstream returned invalid JSON") from exc
    except ValueError as exc:
        raise ToolError("invalid_argument", str(exc)) from exc
    result = (payload.get("data") or {}).get("result") or []
    rows = []
    for series in result:
        labels = {k: v for k, v in (series.get("metric") or {}).items() if k in _KEEP_LABELS}
        value = series.get("value", [None, None])[1]
        try:
            value = round(float(value), 2)
        except (TypeError, ValueError):
            pass
        rows.append({"labels": labels, "value": value})
    kept, omitted = fit_items(rows, budget=1500)
    view.update(
        {
            "query": concrete[0][:160],
            "series": len(rows),
            "values": kept,
            "omitted": omitted,
            "more_queries": max(0, len(concrete) - 1),
        }
    )
    return view


async def _propose_dashboard(ctx, args, role):
    try:
        panels = json.loads(args["panels"])
    except json.JSONDecodeError as exc:
        raise ToolError("invalid_argument", "panels must be a JSON list") from exc
    action = await asyncio.to_thread(
        ctx.actions.propose,
        role,
        "create_dashboard",
        args["title"],
        {"title": args["title"], "panels": panels, "reason": args["reason"]},
    )
    return pending_view(action, ctx.config.console_url)


async def _propose_archive_dashboard(ctx, args, role):
    action = await asyncio.to_thread(
        ctx.actions.propose,
        role,
        "archive_dashboard",
        args["uid"],
        {
            "uid": args["uid"],
            "reason": args["reason"],
            "hard_delete": bool(args.get("hard_delete")),
        },
    )
    return pending_view(action, ctx.config.console_url)


async def _grafana_links(ctx, args, role):
    links = build_links(
        base=ctx.config.grafana_base_url,
        dashboards=ctx.config.grafana_dashboards,
        panels=ctx.config.grafana_panel_ids,
    )
    kept, omitted = fit_items(links, budget=1500)
    return {"links": kept, "omitted": omitted}


TOOLS = (
    Tool(
        "metrics_query",
        OBS,
        "Run a named VictoriaMetrics query. Names: "
        + "; ".join(f"{k} = {v.description}" for k, v in queries.QUERIES.items()),
        {
            "name": Param("string", "named query", required=True, enum=tuple(queries.QUERIES)),
            "instance": Param("string", "optional host or host:port filter", max_length=64),
            "window": Param("string", "time window where the query uses one", enum=queries.WINDOWS),
        },
        _metrics_query,
    ),
    Tool(
        "grafana_dashboards",
        OBS,
        "Every Grafana dashboard the service account can see: title, folder, uid and URL. Not limited to the pinned list.",
        {},
        _grafana_dashboards,
    ),
    Tool(
        "grafana_dashboard",
        OBS,
        "Panels on one Grafana dashboard: id, title, type, row, and the first query. Use this to inspect a dashboard before reporting.",
        {"uid": Param("string", "dashboard uid from grafana_dashboards", required=True, max_length=64)},
        _grafana_dashboard,
    ),
    Tool(
        "grafana_panel",
        OBS,
        "Current values for one panel. Pass vars to bind Grafana template variables before the query runs "
        "(origin_prometheus, Node, NameSpace, Pod, Container). Omitted names use the dashboard default or All (.*). "
        "A query that still contains an unbound $variable is returned as text and is not executed. "
        "time_range is now-1h or from,to such as now-6h,now.",
        {
            "uid": Param("string", "dashboard uid", required=True, max_length=64),
            "panel_id": Param("integer", "panel id from grafana_dashboard", required=True, minimum=0, maximum=100000),
            "vars": Param(
                "object",
                "Template values, for example origin_prometheus=prom1 and Node=.*",
            ),
            "time_range": Param("string", "now-1h, or from,to such as now-6h,now", max_length=64),
        },
        _grafana_panel,
    ),
    Tool("grafana_links", OBS, "Deep links to the pinned Grafana dashboards and panels.", {}, _grafana_links),
    Tool(
        "propose_dashboard",
        OBS,
        "Propose a new Grafana dashboard. A human approves it on the Approvals page; nothing is created before that. "
        "panels is a JSON list of {type, query, title?, instance?, window?}. "
        f"type: {', '.join(grafana_api.PANEL_TYPES)}. query: a metrics_query name. At most 8 panels. "
        "Does not change or delete an existing dashboard.",
        {
            "title": Param("string", "dashboard title", required=True, max_length=80),
            "panels": Param("string", "JSON list of panels", required=True, max_length=4000),
            "reason": Param("string", "one-line reason shown to the approver", required=True, max_length=200),
        },
        _propose_dashboard,
    ),
    Tool(
        "propose_archive_dashboard",
        OBS,
        "Propose archiving one existing Grafana dashboard. A human approves it before anything moves. "
        "hard_delete false moves it to folder _archived. hard_delete true deletes it and cannot be undone. "
        "One uid per call.",
        {
            "uid": Param("string", "dashboard uid from grafana_dashboards", required=True, max_length=64),
            "reason": Param("string", "why this dashboard is being archived", required=True, max_length=200),
            "hard_delete": Param("boolean", "true deletes the dashboard; false moves it to _archived", default=False),
        },
        _propose_archive_dashboard,
    ),
)
