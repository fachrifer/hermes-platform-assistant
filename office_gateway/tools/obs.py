from __future__ import annotations

import json

from office_gateway.grafana_links import build_links
from office_gateway.metrics import instant_query
from office_gateway.reports import queries
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
    Tool("grafana_links", OBS, "Deep links to the configured Grafana dashboards and panels.", {}, _grafana_links),
)
