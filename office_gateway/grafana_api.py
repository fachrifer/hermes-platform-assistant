"""Grafana reads and dashboard creation. The token stays in the gateway."""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any

import httpx

from office_gateway.reports import queries

PANEL_TYPES = ("timeseries", "stat", "table", "gauge")
_TITLE_RE = re.compile(r"^[\w][\w .:_-]{0,79}$")
FOLDER_NAME = "Hermes Fleet"
_MAX_PANELS = 8


class GrafanaClientError(Exception):
    def __init__(self, category: str, detail: str) -> None:
        super().__init__(detail)
        self.category = category
        self.detail = detail


def _request(client: httpx.Client, method: str, url: str, token: str, body: dict | None = None) -> Any:
    try:
        response = client.request(
            method,
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            json=body,
        )
    except httpx.TimeoutException as exc:
        raise GrafanaClientError("timeout", "grafana timed out") from exc
    except httpx.HTTPError as exc:
        raise GrafanaClientError("unreachable", "grafana unreachable") from exc
    if response.status_code in (401, 403):
        raise GrafanaClientError("forbidden", "grafana denied the service account")
    if response.status_code >= 400:
        message = response.text[:160].replace("\n", " ")
        raise GrafanaClientError("invalid_argument", f"grafana HTTP {response.status_code}: {message}")
    if not response.content:
        return {}
    return response.json()


def _client(base: str, token: str) -> httpx.Client:
    if not base or not token:
        raise GrafanaClientError("not_configured", "OFFICE_GRAFANA_BASE_URL and OFFICE_GRAFANA_TOKEN are required")
    return httpx.Client(base_url=base.rstrip("/"), timeout=10.0)


_UID_RE = re.compile(r"^[\w.-]{1,64}$")


_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\[\[([A-Za-z_][A-Za-z0-9_]*)\]\]|\$([A-Za-z_][A-Za-z0-9_]*)")
_RANGE_PART = re.compile(r"^now(?:-[0-9]+[smhdw])?$|^[0-9]{10,13}$")
_SKIP_CURRENT = frozenset({"", "All", "$__all", "None"})


def _exprs(panel: dict) -> list[str]:
    found = []
    for target in panel.get("targets") or []:
        if isinstance(target, dict):
            expr = str(target.get("expr") or "").strip()
            if expr:
                found.append(expr)
    return found


def _walk_panels(panels: Any, row: str, out: list[dict[str, Any]]) -> None:
    for panel in panels or []:
        if not isinstance(panel, dict):
            continue
        if panel.get("type") == "row":
            _walk_panels(panel.get("panels"), str(panel.get("title") or "")[:80], out)
            continue
        try:
            panel_id = int(panel.get("id"))
        except (TypeError, ValueError):
            continue
        item: dict[str, Any] = {
            "id": panel_id,
            "title": str(panel.get("title") or "")[:80],
            "type": str(panel.get("type") or "")[:40],
            "queries": _exprs(panel),
        }
        datasource = _datasource_uid(panel)
        if datasource:
            item["datasource"] = datasource
        if row:
            item["row"] = row
        out.append(item)


def _datasource_uid(panel: dict) -> str:
    source = panel.get("datasource")
    if isinstance(source, str):
        return source.strip()[:128]
    if isinstance(source, dict):
        return str(source.get("uid") or "").strip()[:128]
    return ""


def template_variables(dashboard: dict) -> list[dict[str, Any]]:
    templating = dashboard.get("templating") if isinstance(dashboard.get("templating"), dict) else {}
    rows = []
    for item in templating.get("list") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        current = item.get("current") if isinstance(item.get("current"), dict) else {}
        text = current.get("text") if isinstance(current, dict) else ""
        value = current.get("value") if isinstance(current, dict) else ""
        chosen = _variable_text(text) or _variable_text(value)
        if chosen in _SKIP_CURRENT:
            chosen = ""
        rows.append(
            {
                "name": name[:64],
                "current": chosen[:128],
                "all_value": str(item.get("allValue") or "").strip()[:128],
                "include_all": bool(item.get("includeAll")),
            }
        )
    return rows


def _variable_text(value: Any) -> str:
    if isinstance(value, list):
        return "|".join(str(part).strip() for part in value if str(part).strip())
    return str(value or "").strip()


def parse_time_range(raw: str | None) -> tuple[str, str]:
    text = (raw or "").strip()
    if not text:
        return "now-1h", "now"
    if "," in text:
        start, end = (part.strip() for part in text.split(",", 1))
    else:
        start, end = text, "now"
    if not _RANGE_PART.fullmatch(start) or not _RANGE_PART.fullmatch(end):
        raise ValueError("time_range must look like now-1h or now-6h,now")
    return start, end


def bind_variables(variables: list[dict], caller: dict[str, str] | None, time_from: str) -> dict[str, str]:
    duration = time_from[4:] if time_from.startswith("now-") else "1h"
    bound = {"__range": duration, "__rate_interval": "2m", "__interval": "1m"}
    for item in variables or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        current = str(item.get("current") or "").strip()
        if current and current not in _SKIP_CURRENT:
            bound[name] = current
            continue
        all_value = str(item.get("all_value") or "").strip()
        if all_value:
            bound[name] = all_value
        elif item.get("include_all"):
            bound[name] = ".*"
    for key, value in (caller or {}).items():
        bound[str(key)] = str(value)
    return bound


def substitute(text: str, bound: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        name = match.group(1) or match.group(2) or match.group(3)
        return bound.get(name, match.group(0))

    return _VAR.sub(replace, text or "")


def frames_to_vector(payload: dict) -> dict[str, Any]:
    result = (payload.get("results") or {}).get("A") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        raise GrafanaClientError("invalid_argument", "grafana query returned no result")
    if result.get("error"):
        raise GrafanaClientError("invalid_argument", str(result.get("error"))[:160])
    series = []
    for frame in result.get("frames") or []:
        if not isinstance(frame, dict):
            continue
        fields = (frame.get("schema") or {}).get("fields") or []
        columns = (frame.get("data") or {}).get("values") or []
        for index, field in enumerate(fields):
            if not isinstance(field, dict) or field.get("type") == "time" or field.get("name") == "Time":
                continue
            column = columns[index] if index < len(columns) and isinstance(columns[index], list) else []
            if not column:
                continue
            series.append({"metric": field.get("labels") or {}, "value": [0, column[-1]]})
    return {"status": "success", "data": {"resultType": "vector", "result": series}}


def query_datasource(
    base: str,
    token: str,
    datasource_uid: str,
    expr: str,
    time_from: str,
    time_to: str,
) -> dict[str, Any]:
    if not _UID_RE.fullmatch(datasource_uid or ""):
        raise GrafanaClientError("invalid_argument", "datasource uid is not valid")
    query = (expr or "").strip()
    if not query or len(query) > 4000:
        raise GrafanaClientError("invalid_argument", "panel query is empty or too long")
    body = {
        "from": time_from,
        "to": time_to,
        "queries": [
            {
                "refId": "A",
                "datasource": {"type": "prometheus", "uid": datasource_uid},
                "expr": query,
                "instant": True,
                "range": False,
                "format": "time_series",
                "intervalMs": 60000,
                "maxDataPoints": 1,
            }
        ],
    }
    with _client(base, token) as client:
        payload = _request(client, "POST", "/api/ds/query", token, body)
    if not isinstance(payload, dict):
        raise GrafanaClientError("invalid_argument", "grafana query returned no result")
    return frames_to_vector(payload)


def summarize_panels(dashboard: dict) -> list[dict[str, Any]]:
    panels: list[dict[str, Any]] = []
    _walk_panels(dashboard.get("panels"), "", panels)
    return panels


def get_dashboard(base: str, token: str, uid: str) -> dict[str, Any]:
    if not _UID_RE.fullmatch(uid or ""):
        raise GrafanaClientError("invalid_argument", "uid must be 1-64 letters, numbers, or ._-")
    with _client(base, token) as client:
        payload = _request(client, "GET", f"/api/dashboards/uid/{uid}", token)
    if not isinstance(payload, dict) or not isinstance(payload.get("dashboard"), dict):
        raise GrafanaClientError("invalid_argument", "grafana did not return a dashboard")
    dashboard = payload["dashboard"]
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    url = str(meta.get("url") or f"/d/{uid}")
    if url.startswith("/"):
        url = base.rstrip("/") + url
    return {
        "title": str(dashboard.get("title") or ""),
        "uid": str(dashboard.get("uid") or uid),
        "folder": str(meta.get("folderTitle") or "General"),
        "url": url,
        "panels": summarize_panels(dashboard),
        "variables": template_variables(dashboard),
    }


def search_dashboards(base: str, token: str) -> list[dict[str, str]]:
    with _client(base, token) as client:
        payload = _request(client, "GET", "/api/search?type=dash-db&limit=500", token)
    if not isinstance(payload, list):
        raise GrafanaClientError("invalid_argument", "grafana search did not return a list")
    rows = []
    for item in payload:
        if not isinstance(item, dict) or item.get("type") not in (None, "dash-db"):
            continue
        url = str(item.get("url") or "")
        if url.startswith("/"):
            url = base.rstrip("/") + url
        rows.append(
            {
                "title": str(item.get("title") or ""),
                "uid": str(item.get("uid") or ""),
                "folder": str(item.get("folderTitle") or "General"),
                "url": url,
            }
        )
    rows.sort(key=lambda row: (row["folder"].lower(), row["title"].lower()))
    return rows


def parse_panels(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("panels must be a non-empty list")
    if len(raw) > _MAX_PANELS:
        raise ValueError(f"at most {_MAX_PANELS} panels")
    panels = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each panel must be an object")
        kind = str(item.get("type") or "")
        query = str(item.get("query") or "")
        if kind not in PANEL_TYPES:
            raise ValueError(f"panel type must be one of {', '.join(PANEL_TYPES)}")
        if query not in queries.QUERIES:
            raise ValueError(f"unknown query {query}")
        panel = {"type": kind, "query": query, "title": str(item.get("title") or query)[:80]}
        if item.get("instance"):
            panel["instance"] = str(item["instance"])
        if item.get("window"):
            panel["window"] = str(item["window"])
        queries.render(query, panel.get("instance"), panel.get("window"))
        panels.append(panel)
    return panels


def check_title(title: str) -> str:
    cleaned = title.strip()
    if not _TITLE_RE.fullmatch(cleaned):
        raise ValueError("title must be 1-80 letters, numbers, spaces, or .:_-")
    return cleaned


def render_dashboard(title: str, panels: list[dict[str, str]], datasource_uid: str) -> dict[str, Any]:
    rendered = []
    for index, panel in enumerate(panels):
        expr = queries.render(panel["query"], panel.get("instance"), panel.get("window"))
        datasource: dict[str, str] = {"type": "prometheus"}
        if datasource_uid:
            datasource["uid"] = datasource_uid
        rendered.append(
            {
                "id": index + 1,
                "type": panel["type"],
                "title": panel["title"],
                "gridPos": {"h": 8, "w": 12, "x": (index % 2) * 12, "y": (index // 2) * 8},
                "datasource": datasource,
                "targets": [{"refId": "A", "expr": expr}],
            }
        )
    return {
        "title": title,
        "timezone": "browser",
        "schemaVersion": 39,
        "tags": ["hermes-fleet"],
        "panels": rendered,
    }


def _folder_uid(client: httpx.Client, token: str, configured: str) -> tuple[str, str]:
    if configured:
        return configured, FOLDER_NAME
    payload = _request(client, "GET", "/api/folders", token)
    if isinstance(payload, list):
        for folder in payload:
            if isinstance(folder, dict) and folder.get("title") == FOLDER_NAME and folder.get("uid"):
                return str(folder["uid"]), FOLDER_NAME
    return "", "General"


def _uid(title: str) -> str:
    digest = hashlib.sha256(f"{title}:{time.time_ns()}".encode()).hexdigest()
    return "hf" + digest[:10]


def create_dashboard(
    base: str,
    token: str,
    *,
    title: str,
    panels: list[dict[str, str]],
    folder_uid: str,
    datasource_uid: str,
    message: str,
) -> dict[str, str]:
    with _client(base, token) as client:
        chosen_folder, folder_name = _folder_uid(client, token, folder_uid)
        dashboard = render_dashboard(title, panels, datasource_uid)
        dashboard["uid"] = _uid(title)
        body: dict[str, Any] = {"dashboard": dashboard, "overwrite": False, "message": message[:200]}
        if chosen_folder:
            body["folderUid"] = chosen_folder
        created = _request(client, "POST", "/api/dashboards/db", token, body)
    url = str(created.get("url") or "")
    if url.startswith("/"):
        url = base.rstrip("/") + url
    return {
        "title": title,
        "uid": str(created.get("uid") or dashboard["uid"]),
        "url": url,
        "folder": folder_name,
    }


ARCHIVED_FOLDER = "_archived"


def dashboard_record(base: str, token: str, uid: str) -> dict[str, Any]:
    if not _UID_RE.fullmatch(uid or ""):
        raise GrafanaClientError("invalid_argument", "uid must be 1-64 letters, numbers, or ._-")
    with _client(base, token) as client:
        return _read_dashboard(client, token, uid)


def archive_dashboard(
    base: str,
    token: str,
    *,
    uid: str,
    reason: str,
    hard_delete: bool = False,
) -> dict[str, Any]:
    if not _UID_RE.fullmatch(uid or ""):
        raise GrafanaClientError("invalid_argument", "uid must be 1-64 letters, numbers, or ._-")
    with _client(base, token) as client:
        record = _read_dashboard(client, token, uid)
        if record["folder"] == ARCHIVED_FOLDER and not hard_delete:
            raise GrafanaClientError("not_found", f"dashboard {uid} is already archived")
        if hard_delete:
            _delete_dashboard(client, token, uid)
            return {"uid": uid, "title": record["title"], "folder": record["folder"], "deleted": True}
        folder_uid = _ensure_folder(client, token, ARCHIVED_FOLDER)
        _grant_folder_editors(client, token, folder_uid)
        try:
            _save_into_folder(
                client,
                token,
                {
                    "dashboard": record["dashboard"],
                    "folderUid": folder_uid,
                    "overwrite": True,
                    "message": (reason or "archive dashboard")[:200],
                },
            )
        except GrafanaClientError as exc:
            if "HTTP 500" in exc.detail:
                raise GrafanaClientError(
                    "invalid_argument",
                    f"{exc.detail}. Grafana refused the move into {ARCHIVED_FOLDER}",
                ) from exc
            raise
        return {"uid": uid, "title": record["title"], "folder": ARCHIVED_FOLDER, "deleted": False}


def _read_dashboard(client: httpx.Client, token: str, uid: str) -> dict[str, Any]:
    try:
        payload = _request(client, "GET", f"/api/dashboards/uid/{uid}", token)
    except GrafanaClientError as exc:
        if "HTTP 404" in exc.detail:
            raise GrafanaClientError("not_found", f"dashboard {uid} was not found") from exc
        raise
    if not isinstance(payload, dict) or not isinstance(payload.get("dashboard"), dict):
        raise GrafanaClientError("invalid_argument", "grafana did not return a dashboard")
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    dashboard = payload["dashboard"]
    return {
        "title": str(dashboard.get("title") or uid),
        "uid": str(dashboard.get("uid") or uid),
        "folder": str(meta.get("folderTitle") or "General"),
        "dashboard": dashboard,
    }


def _delete_dashboard(client: httpx.Client, token: str, uid: str) -> None:
    try:
        _request(client, "DELETE", f"/api/dashboards/uid/{uid}", token)
    except GrafanaClientError as exc:
        if "HTTP 404" in exc.detail:
            raise GrafanaClientError("not_found", f"dashboard {uid} was not found") from exc
        raise


def _save_into_folder(client: httpx.Client, token: str, body: dict[str, Any]) -> Any:
    # Grafana returns HTTP 500 "Failed to save dashboard" when the new folder
    # exists but its ACL is not visible yet. A short retry clears that race.
    last: GrafanaClientError | None = None
    for attempt in range(5):
        try:
            return _request(client, "POST", "/api/dashboards/db", token, body)
        except GrafanaClientError as exc:
            last = exc
            if "HTTP 500" not in exc.detail or attempt == 4:
                raise
            time.sleep(0.5 * (attempt + 1))
    assert last is not None
    raise last


def _grant_folder_editors(client: httpx.Client, token: str, folder_uid: str) -> None:
    # Permission 2 is Edit, 4 is Admin. Editors must be able to write the archive folder.
    # A token that cannot change ACLs still continues to the save.
    try:
        _request(
            client,
            "POST",
            f"/api/folders/{folder_uid}/permissions",
            token,
            {
                "items": [
                    {"role": "Viewer", "permission": 1},
                    {"role": "Editor", "permission": 2},
                    {"role": "Admin", "permission": 4},
                ]
            },
        )
    except GrafanaClientError:
        return


def _ensure_folder(client: httpx.Client, token: str, title: str) -> str:
    payload = _request(client, "GET", "/api/folders", token)
    if isinstance(payload, list):
        for folder in payload:
            if isinstance(folder, dict) and folder.get("title") == title and folder.get("uid"):
                return str(folder["uid"])
    created = _request(client, "POST", "/api/folders", token, {"title": title})
    folder_uid = str(created.get("uid") or "") if isinstance(created, dict) else ""
    if not folder_uid:
        raise GrafanaClientError("invalid_argument", "grafana did not create the archive folder")
    return folder_uid
