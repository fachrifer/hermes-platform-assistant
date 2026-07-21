"""Quota and provider usage reporting."""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings import settings
from connectors.tavily import TavilyConnector

logger = logging.getLogger("hermes.quota")


def _number(value: int | float) -> str:
    return f"{value:,}".replace(",", ".")


def format_tavily_usage(data: dict) -> str:
    key = data.get("key", {}) if isinstance(data, dict) else {}
    usage = key.get("usage")
    limit = key.get("limit")
    if not isinstance(usage, (int, float)) or not isinstance(limit, (int, float)):
        return "⚠️ Tavily: quota belum tersedia."
    remaining = max(0, limit - usage)
    return (
        f"✅ Tavily: {_number(usage)} / {_number(limit)} credits terpakai\n"
        f"Sisa: {_number(remaining)} credits"
    )


class QuotaService:
    def __init__(self, tavily: TavilyConnector | None = None):
        self.tavily = tavily or TavilyConnector()

    def tavily_usage(self) -> dict:
        return self.tavily.usage()

    def gemini_status(self) -> dict:
        status = {
            "configured": bool(settings.gemini_api_key),
            "model": settings.gemini_model,
            "official_quota": False,
            "reason": "",
        }
        if not status["configured"]:
            status["reason"] = "GEMINI_API_KEY belum dikonfigurasi."
            return status
        if not settings.gemini_quota_project_id or not settings.gemini_quota_credentials:
            status["reason"] = (
                "Quota resmi belum dikonfigurasi. Isi GEMINI_QUOTA_PROJECT_ID "
                "dan GEMINI_QUOTA_CREDENTIALS."
            )
            return status
        if not Path(settings.gemini_quota_credentials).exists():
            status["reason"] = "File service account quota belum ditemukan."
            return status

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            credentials = service_account.Credentials.from_service_account_file(
                settings.gemini_quota_credentials,
                scopes=["https://www.googleapis.com/auth/cloud-platform.read-only"],
            )
            service = build("serviceusage", "v1beta1", credentials=credentials, cache_discovery=False)
            parent = (
                f"projects/{settings.gemini_quota_project_id}/services/"
                "generativelanguage.googleapis.com"
            )
            response = service.services().consumerQuotaMetrics().list(parent=parent).execute()
            status["official_quota"] = True
            status["metric_count"] = len(response.get("metrics", []))
            status["reason"] = "Metadata quota resmi tersedia; pemakaian aktual tidak selalu dipublikasikan."
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini quota gagal dibaca: %s", exc)
            status["reason"] = "Quota resmi tidak dapat dibaca dengan credential saat ini."
        return status

    def render(self) -> str:
        tavily = format_tavily_usage(self.tavily_usage())
        gemini = self.gemini_status()
        gemini_lines = [
            f"{'✅' if gemini['configured'] else '❌'} Gemini: "
            f"{'API key terkonfigurasi' if gemini['configured'] else 'API key belum dikonfigurasi'}",
            f"Model: {gemini['model']}",
        ]
        if gemini["official_quota"]:
            gemini_lines.append(
                f"Quota resmi: tersedia ({gemini.get('metric_count', 0)} metric)"
            )
        else:
            gemini_lines.append(f"Quota resmi: {gemini['reason']}")
        return "📊 Quota layanan\n\n" + tavily + "\n\n" + "\n".join(gemini_lines)
