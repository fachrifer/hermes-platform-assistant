"""Dashboard reports for the obs specialist.

``grafana_panel`` answers about one panel and only runs its first query. A report has to read every
panel of a dashboard and every query of each panel, compare the numbers with the thresholds the
dashboard already defines, and leave a file a person can open. This module does that and nothing else:

* ``build_report``  - read dashboards, run their queries over a range, judge the values (all blocking I/O).
* ``agent_view``    - the small JSON the agent gets back (the tool result is capped at about 14 KB).
* ``render_html``   - the full report written to the shared reports folder.

Read-only against Grafana: it only calls ``GET /api/dashboards/uid``, ``GET /api/datasources`` and
``POST /api/ds/query``.
"""
from __future__ import annotations

import html
import math
import re
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from office_gateway import grafana_api
from office_gateway.grafana_api import GrafanaClientError

# Dashboards the report knows by name. Anything else is reachable with an explicit uid.
DASHBOARDS: dict[str, str] = {
    "fleet": "fleet-overview",
    "milvus": "de68d706-4a90-4ad8-b338-fb5d94b09af0",
    "insightface": "Insightface-prod-v2",
}
SCOPES = ("all", *DASHBOARDS)

# First directory that accepts a write wins. /reports is the folder every specialist shares with the VM.
REPORT_DIRS = ("/reports", "/var/lib/hermes-office-gateway/reports", "/tmp/hermes-reports")
VM_REPORTS_DIR = "/home/timai/hermes-assistant/reports"

WIB = timezone(timedelta(hours=7))
DEADLINE_SECONDS = 9.0
QUERY_TIMEOUT_SECONDS = 8.0
MAX_QUERIES_PER_DASHBOARD = 120
MAX_SERIES_PER_QUERY = 12
SPARK_POINTS = 48
LEVELS = {"critical": 3, "warning": 2, "ok": 1, "info": 0}
SKIP_TYPES = frozenset({"row", "text", "logs", "news", "dashlist", "alertlist", "traces", "nodeGraph", "flamegraph"})
# A bare log selector would pull thousands of lines. Only LogQL that aggregates is summarized.
_LOGQL_METRIC = re.compile(
    r"^\s*(sum|avg|min|max|count|topk|bottomk|rate|count_over_time|bytes_over_time|bytes_rate|"
    r"quantile_over_time|absent_over_time|sort|sort_desc)\b"
)
_RED = frozenset({"#f2495c", "#e02f44", "#c4162a", "#fa6400"})
_AMBER = frozenset({"#eab839", "#ff9830", "#fade2a", "#ef843c", "#ff780a"})


# --------------------------------------------------------------------------------------- formatting


def _num(value: float, decimals: int | None = None) -> str:
    if decimals is None:
        decimals = 0 if abs(value) >= 100 else 1 if abs(value) >= 10 else 2
    text = f"{value:,.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _scaled(value: float, base: int, suffixes: tuple[str, ...], decimals: int | None) -> str:
    index = 0
    while abs(value) >= base and index < len(suffixes) - 1:
        value /= base
        index += 1
    return f"{_num(value, decimals)} {suffixes[index]}".strip()


def _duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {rest % 60}s"


def format_value(value: float | None, unit: str = "", decimals: int | None = None) -> str:
    if value is None or isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return "-"
    unit = (unit or "").strip()
    if unit == "percent":
        return f"{_num(value, decimals)}%"
    if unit == "percentunit":
        return f"{_num(value * 100, decimals)}%"
    if unit == "bytes":
        return _scaled(value, 1024, ("B", "KiB", "MiB", "GiB", "TiB", "PiB"), decimals)
    if unit == "decbytes":
        return _scaled(value, 1000, ("B", "kB", "MB", "GB", "TB", "PB"), decimals)
    if unit in ("Bps", "binBps"):
        base = 1000 if unit == "Bps" else 1024
        return _scaled(value, base, ("B/s", "kB/s", "MB/s", "GB/s"), decimals)
    if unit == "reqps":
        return f"{_num(value, decimals)} req/s"
    if unit in ("ms", "s", "watt", "celsius"):
        suffix = {"ms": "ms", "s": "s", "watt": "W", "celsius": "°C"}[unit]
        return f"{_num(value, decimals)} {suffix}" if unit != "celsius" else f"{_num(value, decimals)}{suffix}"
    if unit in ("dtdhms", "dtdurations"):
        return _duration(value)
    if unit in ("", "short", "none"):
        # Counts read better whole ("37,889") than rounded ("38K"); only huge or fractional numbers are scaled.
        if float(value).is_integer() and abs(value) < 1e9:
            return f"{int(value):,}"
        return _scaled(value, 1000, ("", "K", "M", "B"), decimals).replace(" ", "")
    return f"{_num(value, decimals)} {unit}"


# --------------------------------------------------------------------------------------- judging


def _color_level(color: str | None) -> str:
    text = (color or "").strip().lower()
    if text.startswith(("red", "dark-red", "semi-dark-red", "light-red")) or text in _RED:
        return "critical"
    if "orange" in text or "yellow" in text or text in _AMBER:
        return "warning"
    if "green" in text:
        return "ok"
    return "info"


def _mapped(mappings: Any, value: float) -> tuple[str, str] | None:
    for mapping in mappings or []:
        if not isinstance(mapping, dict):
            continue
        kind = mapping.get("type")
        if kind == "value":
            hit = (mapping.get("options") or {}).get(_num_key(value))
            if isinstance(hit, dict) and hit.get("text") is not None:
                return str(hit["text"]), _color_level(hit.get("color"))
        elif kind == "range":
            options = mapping.get("options") or {}
            low, high = options.get("from"), options.get("to")
            result = options.get("result") or {}
            if low is not None and high is not None and low <= value <= high and result.get("text") is not None:
                return str(result["text"]), _color_level(result.get("color"))
    return None


def _num_key(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _threshold_level(defaults: dict, value: float) -> str:
    thresholds = defaults.get("thresholds") if isinstance(defaults.get("thresholds"), dict) else {}
    steps = [s for s in thresholds.get("steps") or [] if isinstance(s, dict)]
    if len(steps) < 2:
        return "info"
    probe = value
    if thresholds.get("mode") == "percentage":
        low, high = defaults.get("min"), defaults.get("max")
        if low is None:
            low = 0
        if high is None or high == low:
            return "info"
        probe = (value - low) / (high - low) * 100
    chosen = steps[0]
    for step in steps[1:]:
        limit = step.get("value")
        if limit is not None and probe >= limit:
            chosen = step
    return _color_level(chosen.get("color"))


def _uses_thresholds(panel: dict) -> bool:
    kind = panel.get("type")
    if kind in ("stat", "gauge", "bargauge"):
        return True
    if kind in ("timeseries", "graph"):
        custom = ((panel.get("fieldConfig") or {}).get("defaults") or {}).get("custom") or {}
        style = (custom.get("thresholdsStyle") or {}).get("mode", "off")
        return style not in ("off", "")
    return False


def judge(panel: dict, value: float | None, unit: str, decimals: int | None) -> tuple[str, str]:
    """Return the text to show for a value and how worried to be about it."""
    if value is None:
        return "-", "info"
    defaults = (panel.get("fieldConfig") or {}).get("defaults") or {}
    mapped = _mapped(defaults.get("mappings"), value)
    if mapped:
        return mapped
    text = format_value(value, unit, decimals)
    level = _threshold_level(defaults, value) if _uses_thresholds(panel) else "info"
    return text, level


# --------------------------------------------------------------------------------------- Grafana I/O


def fetch_dashboard(base: str, token: str, uid: str) -> dict[str, Any]:
    if not grafana_api._UID_RE.fullmatch(uid or ""):
        raise GrafanaClientError("invalid_argument", "uid must be 1-64 letters, numbers, or ._-")
    with grafana_api._client(base, token) as client:
        payload = grafana_api._request(client, "GET", f"/api/dashboards/uid/{uid}", token)
    if not isinstance(payload, dict) or not isinstance(payload.get("dashboard"), dict):
        raise GrafanaClientError("invalid_argument", "grafana did not return a dashboard")
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    url = str(meta.get("url") or f"/d/{uid}")
    if url.startswith("/"):
        url = base.rstrip("/") + url
    return {"dashboard": payload["dashboard"], "url": url, "folder": str(meta.get("folderTitle") or "General")}


def run_queries(
    base: str,
    token: str,
    ds_uid: str,
    ds_type: str,
    queries: list[tuple[str, str]],
    time_from: str,
    time_to: str,
    step_seconds: int,
) -> dict[str, dict]:
    """Range-query ``queries`` ([(ref, expr)]) on one datasource. Returns ref -> {"series": [...]} or {"error": str}."""
    if not grafana_api._UID_RE.fullmatch(ds_uid or ""):
        return {ref: {"error": "datasource uid is not valid"} for ref, _ in queries}
    body_queries = []
    for ref, expr in queries:
        item: dict[str, Any] = {
            "refId": ref,
            "datasource": {"type": ds_type, "uid": ds_uid},
            "expr": expr,
            "instant": False,
            "range": True,
            "intervalMs": step_seconds * 1000,
            "maxDataPoints": 120,
        }
        if ds_type == "loki":
            item["queryType"] = "range"
            item["step"] = f"{step_seconds}s"
        else:
            item["format"] = "time_series"
        body_queries.append(item)
    body = {"from": time_from, "to": time_to, "queries": body_queries}
    with httpx.Client(base_url=base.rstrip("/"), timeout=QUERY_TIMEOUT_SECONDS) as client:
        payload = grafana_api._request(client, "POST", "/api/ds/query", token, body)
    results = (payload.get("results") or {}) if isinstance(payload, dict) else {}
    out: dict[str, dict] = {}
    for ref, _ in queries:
        result = results.get(ref)
        if not isinstance(result, dict):
            out[ref] = {"series": []}
        elif result.get("error"):
            out[ref] = {"error": str(result["error"])[:160]}
        else:
            out[ref] = {"series": _series(result)}
    return out


def _series(result: dict) -> list[dict]:
    rows: list[dict] = []
    for frame in result.get("frames") or []:
        if not isinstance(frame, dict):
            continue
        fields = (frame.get("schema") or {}).get("fields") or []
        columns = (frame.get("data") or {}).get("values") or []
        for index, field in enumerate(fields):
            if not isinstance(field, dict) or field.get("type") == "time":
                continue
            column = columns[index] if index < len(columns) and isinstance(columns[index], list) else []
            values = [
                float(x) for x in column
                if isinstance(x, (int, float)) and not isinstance(x, bool) and not math.isnan(x) and not math.isinf(x)
            ]
            if not values:
                continue
            rows.append({"labels": field.get("labels") or {}, "values": values})
    return rows


# --------------------------------------------------------------------------------------- building


def range_seconds(time_from: str, time_to: str) -> int:
    def relative(text: str) -> int | None:
        match = re.fullmatch(r"now(?:-([0-9]+)([smhdw]))?", text)
        if not match:
            return None
        if not match.group(1):
            return 0
        return int(match.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]

    start, end = relative(time_from), relative(time_to)
    if start is not None and end is not None and start > end:
        return start - end
    if time_from.isdigit() and time_to.isdigit():
        gap = abs(int(time_to) - int(time_from))
        return gap // 1000 if len(time_from) >= 13 else gap
    return 6 * 3600


def _legend(template: str, labels: dict, fallback: str) -> str:
    template = (template or "").strip()
    if template and template != "__auto":
        return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(labels.get(m.group(1), "")), template).strip() or fallback
    shown = [str(v) for k, v in labels.items() if k != "__name__"]
    return " ".join(shown) if shown else fallback


def _spark(values: list[float]) -> list[float]:
    if len(values) <= SPARK_POINTS:
        return [round(v, 4) for v in values]
    stride = len(values) / SPARK_POINTS
    return [round(values[int(i * stride)], 4) for i in range(SPARK_POINTS)]


def _walk(panels: Any, row: str, out: list[tuple[dict, str]]) -> None:
    for panel in panels or []:
        if not isinstance(panel, dict):
            continue
        if panel.get("type") == "row":
            title = str(panel.get("title") or "")
            _walk(panel.get("panels"), title, out)
            row = title  # panels that follow an open row belong to it
            continue
        out.append((panel, row))


def _target_datasource(panel: dict, target: dict, bound: dict[str, str]) -> tuple[str, str]:
    source = target.get("datasource") or panel.get("datasource") or {}
    if isinstance(source, str):
        source = {"uid": source}
    uid = grafana_api.substitute(str(source.get("uid") or ""), bound).strip()
    kind = str(source.get("type") or "prometheus").lower()
    return uid, kind


def _unresolved(text: str) -> bool:
    return "$" in text or "[[" in text


def build_dashboard(base: str, token: str, uid: str, time_from: str, time_to: str) -> dict[str, Any]:
    loaded = fetch_dashboard(base, token, uid)
    raw = loaded["dashboard"]
    variables = grafana_api.template_variables(raw)
    grafana_api.apply_datasource_defaults(base, token, variables)
    duration = range_seconds(time_from, time_to)
    step = max(60, duration // 90)
    bound = grafana_api.bind_variables(variables, None, time_from)
    # rate() needs a window that spans a few steps or it skips samples.
    bound["__rate_interval"] = f"{max(2, (4 * step + 59) // 60)}m"

    found: list[tuple[dict, str]] = []
    _walk(raw.get("panels"), "", found)

    records: list[dict] = []
    jobs: dict[tuple[str, str], list[tuple[str, str, dict, dict, int]]] = {}
    budget = MAX_QUERIES_PER_DASHBOARD
    for panel, row in found:
        record = {
            "id": panel.get("id"), "title": str(panel.get("title") or "")[:90], "type": str(panel.get("type") or ""),
            # A row title can carry a variable ("Status - $server").
            "row": grafana_api.substitute(row, bound)[:60], "level": "info", "series": [], "state": "", "note": "",
        }
        records.append(record)
        if panel.get("type") in SKIP_TYPES:
            record["state"], record["note"] = "skipped", f"{panel.get('type')} panel"
            continue
        targets = [t for t in panel.get("targets") or [] if isinstance(t, dict) and t.get("expr") and not t.get("hide")]
        if not targets:
            record["state"], record["note"] = "skipped", "no query"
            continue
        record["_targets"] = len(targets)
        for position, target in enumerate(targets):
            expr = grafana_api.substitute(str(target["expr"]), bound)
            ds_uid, ds_type = _target_datasource(panel, target, bound)
            if _unresolved(expr) or _unresolved(ds_uid) or not ds_uid:
                record["note"] = "a variable has no value"
                continue
            if ds_type == "loki" and not _LOGQL_METRIC.match(expr):
                record["note"] = "log lines, not summarized"
                continue
            if budget <= 0:
                record["note"] = "query limit reached"
                continue
            budget -= 1
            jobs.setdefault((ds_uid, ds_type), []).append((f"q{len(jobs)}_{len(records)}_{position}", expr, panel, record, position))

    by_ref: dict[str, tuple[dict, dict, int]] = {}
    for (ds_uid, ds_type), items in jobs.items():
        queries = [(ref, expr) for ref, expr, _, _, _ in items]
        for ref, _, panel, record, position in items:
            by_ref[ref] = (panel, record, position)
        try:
            answers = run_queries(base, token, ds_uid, ds_type, queries, time_from, time_to, step)
        except GrafanaClientError as exc:
            answers = {ref: {"error": exc.detail} for ref, _ in queries}
        for ref, answer in answers.items():
            panel, record, position = by_ref[ref]
            _absorb(panel, record, position, answer)

    for record in records:
        record.pop("_targets", None)
        if record["state"] == "skipped":
            continue
        if record["series"]:
            record["state"] = "ok"
            record["level"] = max((s["level"] for s in record["series"]), key=lambda lv: LEVELS[lv])
        elif record["note"] and "error" in record["note"]:
            record["state"] = "error"
        elif not record["state"]:
            record["state"] = "no_data" if not record["note"] else "skipped"
    return {"title": str(raw.get("title") or uid), "uid": uid, "url": loaded["url"], "folder": loaded["folder"],
            "panels": records}


def _absorb(panel: dict, record: dict, position: int, answer: dict) -> None:
    if "error" in answer:
        record["note"] = f"query error: {answer['error']}"
        return
    defaults = (panel.get("fieldConfig") or {}).get("defaults") or {}
    unit = str(defaults.get("unit") or "")
    if panel.get("type") == "table" and record.get("_targets", 1) > 1:
        unit = ""  # a joined table mixes units; the panel unit would be wrong for some columns
    decimals = defaults.get("decimals")
    decimals = decimals if isinstance(decimals, int) else None
    legend = ""
    targets = [t for t in panel.get("targets") or [] if isinstance(t, dict) and t.get("expr") and not t.get("hide")]
    if position < len(targets):
        legend = str(targets[position].get("legendFormat") or "")
    for item in answer["series"][:MAX_SERIES_PER_QUERY]:
        values = item["values"]
        last = values[-1]
        text, level = judge(panel, last, unit, decimals)
        # Min/max/avg of a value-mapped status (UP = 1, NO DATA = -1) would print raw codes, so leave them out.
        mapped = bool(defaults.get("mappings"))
        record["series"].append({
            "name": _legend(legend, item["labels"], record["title"])[:80],
            "text": text,
            "level": level,
            "last": last,
            "min": "-" if mapped else format_value(min(values), unit, decimals),
            "max": "-" if mapped else format_value(max(values), unit, decimals),
            "avg": "-" if mapped else format_value(sum(values) / len(values), unit, decimals),
            "spark": _spark(values) if len(values) > 3 else [],
        })


def build_report(base: str, token: str, uids: list[str], time_from: str, time_to: str) -> dict[str, Any]:
    """Read every dashboard in parallel. A dashboard that fails becomes a section with an error, not a failed report."""
    started = datetime.now(timezone.utc)
    pool = ThreadPoolExecutor(max_workers=max(1, len(uids)))
    futures = {pool.submit(build_dashboard, base, token, uid, time_from, time_to): uid for uid in uids}
    done, pending = wait(futures, timeout=DEADLINE_SECONDS)
    pool.shutdown(wait=False, cancel_futures=True)
    sections: list[dict] = []
    first_error: GrafanaClientError | None = None
    for future, uid in futures.items():
        if future in pending:
            sections.append({"title": uid, "uid": uid, "url": "", "folder": "", "panels": [], "error": "timed out"})
            continue
        try:
            sections.append(future.result())
        except GrafanaClientError as exc:
            first_error = first_error or exc
            sections.append({"title": uid, "uid": uid, "url": "", "folder": "", "panels": [], "error": exc.detail})
        except Exception as exc:  # noqa: BLE001 - one broken dashboard must not sink the report
            sections.append({"title": uid, "uid": uid, "url": "", "folder": "", "panels": [],
                             "error": f"{type(exc).__name__}"})
    if first_error and all(section.get("error") for section in sections):
        raise first_error
    report = {
        "generated": started.astimezone(WIB).strftime("%Y-%m-%d %H:%M WIB"),
        "generated_utc": started.strftime("%H:%M UTC"),
        "range": f"{time_from} to {time_to}",
        "dashboards": sections,
    }
    report["summary"] = summarize(report)
    return report


def summarize(report: dict) -> dict[str, int]:
    counts = {"dashboards": len(report["dashboards"]), "panels": 0, "critical": 0, "warning": 0, "ok": 0, "info": 0,
              "no_data": 0, "skipped": 0, "errors": 0}
    for section in report["dashboards"]:
        if section.get("error"):
            counts["errors"] += 1
        for panel in section["panels"]:
            counts["panels"] += 1
            if panel["state"] == "ok":
                counts[panel["level"]] += 1
            elif panel["state"] == "no_data":
                counts["no_data"] += 1
            elif panel["state"] == "error":
                counts["errors"] += 1
            else:
                counts["skipped"] += 1
    return counts


# --------------------------------------------------------------------------------------- outputs


def _worst(panel: dict) -> dict:
    return max(panel["series"], key=lambda s: LEVELS[s["level"]])


def attention_items(report: dict) -> list[dict]:
    rows = []
    for section in report["dashboards"]:
        for panel in section["panels"]:
            if panel["state"] == "ok" and panel["level"] in ("critical", "warning"):
                bad = [s for s in panel["series"] if s["level"] == panel["level"]]
                shown = "; ".join(f"{s['name']}: {s['text']}" if s["name"] != panel["title"] else s["text"] for s in bad[:3])
                rows.append({"level": panel["level"], "dashboard": section["title"], "panel": panel["title"],
                             "id": panel["id"], "value": shown[:160]})
            elif panel["state"] in ("no_data", "error"):
                rows.append({"level": panel["state"], "dashboard": section["title"], "panel": panel["title"],
                             "id": panel["id"], "value": (panel["note"] or "no data in range")[:160]})
    order = {"critical": 0, "warning": 1, "error": 2, "no_data": 3}
    rows.sort(key=lambda r: order[r["level"]])
    return rows


def _headline(panel: dict) -> str:
    series = panel["series"]
    if len(series) == 1:
        return f"{panel['title']}: {series[0]['text']}"
    inner = ", ".join(f"{s['name']} {s['text']}" for s in series[:4])
    return f"{panel['title']}: {inner}" + (" ..." if len(series) > 4 else "")


def agent_view(report: dict, saved: dict[str, Any]) -> dict[str, Any]:
    from office_gateway.tools.core import fit_items

    attention, attention_omitted = fit_items(attention_items(report), budget=4800)
    sections = []
    for section in report["dashboards"]:
        stat_like = [p for p in section["panels"] if p["state"] == "ok" and p["type"] in ("stat", "gauge", "bargauge")]
        highlights, _ = fit_items([_headline(p)[:150] for p in stat_like], budget=1700)
        item = {
            "title": section["title"], "url": section["url"],
            "panels": len(section["panels"]),
            "critical": sum(1 for p in section["panels"] if p["state"] == "ok" and p["level"] == "critical"),
            "warning": sum(1 for p in section["panels"] if p["state"] == "ok" and p["level"] == "warning"),
            "no_data": sum(1 for p in section["panels"] if p["state"] == "no_data"),
            "highlights": highlights,
        }
        if section.get("error"):
            item["error"] = section["error"]
        sections.append(item)
    return {
        "generated": report["generated"], "range": report["range"], "summary": report["summary"],
        "attention": attention, "attention_omitted": attention_omitted,
        "dashboards": sections, "report": saved,
    }


_CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:32px;color:#1a1a2e;background:#fafbfc}
h1{font-size:22px;border-bottom:3px solid #4361ee;padding-bottom:8px}
h2{font-size:17px;margin-top:30px;color:#4361ee}h3{font-size:14px;margin:18px 0 4px;color:#555}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px;background:#fff}
th,td{border:1px solid #d9dce3;padding:6px 9px;text-align:left;vertical-align:top}
th{background:#eef1f8;font-weight:600}tr:nth-child(even){background:#f7f8fb}
.badge{display:inline-block;padding:2px 9px;border-radius:10px;font-size:12px;font-weight:600;color:#fff;margin-right:6px}
.critical{color:#c53030;font-weight:600}.warning{color:#b7791f;font-weight:600}.ok{color:#1b7f3b}.info,.muted{color:#6b7280}
.b-critical{background:#c53030}.b-warning{background:#d69e2e}.b-ok{background:#2f855a}.b-info{background:#6b7280}
.meta{color:#6b7280;font-size:13px}svg{vertical-align:middle}
"""


def _spark_svg(values: list[float], level: str) -> str:
    if len(values) < 4:
        return ""
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    width, height = 110, 22
    # A flat series sits in the middle instead of hugging the bottom edge.
    points = " ".join(
        f"{i * width / (len(values) - 1):.1f},"
        f"{height / 2 if high == low else height - 2 - (v - low) / span * (height - 4):.1f}"
        for i, v in enumerate(values)
    )
    color = {"critical": "#c53030", "warning": "#d69e2e"}.get(level, "#4361ee")
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<polyline fill="none" stroke="{color}" stroke-width="1.5" points="{points}"/></svg>')


def render_html(report: dict) -> str:
    esc = html.escape
    summary = report["summary"]
    out = [
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        f"<title>Dashboard report - {esc(report['generated'])}</title><style>{_CSS}</style></head><body>",
        "<h1>Dashboard report</h1>",
        f"<p class=\"meta\">Generated {esc(report['generated'])} ({esc(report['generated_utc'])}). "
        f"Range {esc(report['range'])}. Read-only: values come from Grafana's datasources.</p>",
        "<p>"
        f"<span class=\"badge b-critical\">{summary['critical']} critical</span>"
        f"<span class=\"badge b-warning\">{summary['warning']} warning</span>"
        f"<span class=\"badge b-ok\">{summary['ok']} ok</span>"
        f"<span class=\"badge b-info\">{summary['info']} no threshold</span>"
        f"<span class=\"badge b-info\">{summary['no_data']} no data</span>"
        f"<span class=\"badge b-info\">{summary['skipped']} skipped</span></p>",
    ]
    rows = attention_items(report)
    out.append("<h2>Needs attention</h2>")
    if rows:
        out.append("<table><tr><th>Status</th><th>Dashboard</th><th>Panel</th><th>Value or reason</th></tr>")
        for row in rows:
            label = {"critical": "CRITICAL", "warning": "WARNING", "error": "ERROR", "no_data": "NO DATA"}[row["level"]]
            css = row["level"] if row["level"] in ("critical", "warning") else "muted"
            out.append(f"<tr><td class=\"{css}\">{label}</td><td>{esc(row['dashboard'])}</td>"
                       f"<td>{esc(row['panel'])}</td><td>{esc(row['value'])}</td></tr>")
        out.append("</table>")
    else:
        out.append("<p class=\"ok\">Nothing is outside the thresholds the dashboards define.</p>")
    for section in report["dashboards"]:
        link = f" <a href=\"{esc(section['url'])}\">open in Grafana</a>" if section["url"] else ""
        out.append(f"<h2>{esc(section['title'])}</h2><p class=\"meta\">{esc(section['folder'])}{link}</p>")
        if section.get("error"):
            out.append(f"<p class=\"critical\">Could not read this dashboard: {esc(section['error'])}</p>")
            continue
        current_row = None
        out.append("<table><tr><th>Panel</th><th>Now</th><th>Min</th><th>Max</th><th>Avg</th><th>Trend</th></tr>")
        for panel in section["panels"]:
            if panel["row"] != current_row:
                current_row = panel["row"]
                out.append(f"<tr><th colspan=\"6\">{esc(current_row or 'General')}</th></tr>")
            if panel["state"] != "ok":
                reason = {"no_data": "no data in range", "error": panel["note"], "skipped": panel["note"]}[panel["state"]]
                out.append(f"<tr><td>{esc(panel['title'])}</td><td colspan=\"5\" class=\"muted\">{esc(reason)}</td></tr>")
                continue
            for index, item in enumerate(panel["series"]):
                name = esc(panel["title"]) if index == 0 else ""
                if len(panel["series"]) > 1 or item["name"] != panel["title"]:
                    name += f"<br><span class=\"muted\">{esc(item['name'])}</span>" if name else f"<span class=\"muted\">{esc(item['name'])}</span>"
                out.append(
                    f"<tr><td>{name}</td><td class=\"{item['level']}\">{esc(item['text'])}</td>"
                    f"<td>{esc(item['min'])}</td><td>{esc(item['max'])}</td><td>{esc(item['avg'])}</td>"
                    f"<td>{_spark_svg(item['spark'], item['level'])}</td></tr>"
                )
        out.append("</table>")
    out.append("<p class=\"meta\">Levels come from the thresholds and value mappings saved in each dashboard. "
               "A panel without thresholds is shown but not judged.</p></body></html>")
    return "".join(out)
