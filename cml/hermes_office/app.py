"""FastAPI Manager UI for CML hermes-office."""

from __future__ import annotations

import html
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from cml.hermes_office.store import OfficeStore

_TEMPLATE = (Path(__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")


def _render(store: OfficeStore) -> str:
    health_payload = store.latest_health()
    reports = store.list_reports(limit=10)
    logs = store.list_recent_logs(limit=50)
    latest = store.latest_report("status") or (reports[0] if reports else None)

    if health_payload and health_payload.get("services"):
        health_rows = []
        for service in health_payload["services"]:
            status = service.get("status", "unknown")
            css = "ok" if status in {"ok", "warning"} else "bad"
            latency = service.get("latency_ms")
            latency_html = (
                f' <span class="muted">({float(latency):.1f} ms)</span>' if latency is not None else ""
            )
            health_rows.append(
                "<li><strong>{name}</strong>: <span class=\"{css}\">{status}</span>{latency}</li>".format(
                    name=html.escape(str(service.get("name", ""))),
                    css=css,
                    status=html.escape(str(status)),
                    latency=latency_html,
                )
            )
        health_html = (
            "<ul>"
            + "".join(health_rows)
            + f'</ul><p class="muted">Observed: {html.escape(str(health_payload.get("observed_at", "")))}</p>'
        )
    else:
        health_html = '<p class="muted">No health samples yet.</p>'

    if latest:
        latest_html = f"<pre>{html.escape(latest['content'])}</pre>"
    else:
        latest_html = '<p class="muted">No reports yet.</p>'

    if reports:
        report_html = "<ul>" + "".join(
            f"<li><strong>{html.escape(r['period'])}</strong> "
            f"<span class=\"muted\">{html.escape(r['created_at'])}</span></li>"
            for r in reports
        ) + "</ul>"
    else:
        report_html = '<p class="muted">Empty.</p>'

    if logs:
        log_body = "\n".join(
            f"[{html.escape(item['service'])}] {html.escape(item['line'])}" for item in logs
        )
        logs_html = f"<pre>{log_body}</pre>"
    else:
        logs_html = '<p class="muted">No logs stored.</p>'

    return (
        _TEMPLATE.replace("{{HEALTH}}", health_html)
        .replace("{{LATEST}}", latest_html)
        .replace("{{REPORTS}}", report_html)
        .replace("{{LOGS}}", logs_html)
    )


def create_app(store: OfficeStore) -> FastAPI:
    app = FastAPI(title="Hermes Office CML", docs_url=None, redoc_url=None)
    app.state.store = store

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/status")
    async def api_status():
        return {
            "health": store.latest_health(),
            "latest_report": store.latest_report("status") or store.list_reports(limit=1)[:1],
        }

    @app.get("/api/reports")
    async def api_reports(limit: int = 20):
        return store.list_reports(limit=limit)

    @app.get("/api/logs")
    async def api_logs(service: str | None = None, limit: int = 100):
        return store.list_recent_logs(service, limit=limit)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(_render(store))

    return app
