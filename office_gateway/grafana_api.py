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
