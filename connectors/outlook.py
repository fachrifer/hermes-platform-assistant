"""Microsoft Outlook / Graph connector (read-only) for agenda.

Uses MSAL device-code flow with a persistent token cache. Requires an Azure app
registration with delegated Calendars.Read permission. First login is done once
interactively (see README); afterwards the cached refresh token is used silently.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from pathlib import Path

import requests

from config.settings import settings
from core.connector import Connector

logger = logging.getLogger("hermes.outlook")

SCOPES = ["Calendars.Read"]
GRAPH = "https://graph.microsoft.com/v1.0"


def _load_cache():
    from msal import SerializableTokenCache

    cache = SerializableTokenCache()
    p = Path(settings.ms_token_cache)
    if p.exists():
        cache.deserialize(p.read_text(encoding="utf-8"))
    return cache


def _save_cache(cache) -> None:
    if cache.has_state_changed:
        p = Path(settings.ms_token_cache)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(cache.serialize(), encoding="utf-8")


def _acquire_token_silent() -> str | None:
    """Acquire an access token silently from the cache. None if not logged in."""
    from msal import PublicClientApplication

    if not settings.ms_client_id:
        return None
    cache = _load_cache()
    app = PublicClientApplication(
        settings.ms_client_id,
        authority=f"https://login.microsoftonline.com/{settings.ms_tenant_id}",
        token_cache=cache,
    )
    accounts = app.get_accounts()
    if not accounts:
        return None
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    _save_cache(cache)
    if result and "access_token" in result:
        return result["access_token"]
    return None


class OutlookConnector(Connector):
    name = "Outlook"
    icon = "📆"

    def is_available(self) -> bool:
        return (
            settings.ms_outlook_enabled
            and bool(settings.ms_client_id)
            and Path(settings.ms_token_cache).exists()
        )

    async def health_check(self) -> bool:
        if not self.is_available():
            return False
        try:
            token = await asyncio.to_thread(_acquire_token_silent)
            return bool(token)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Outlook health check gagal: %s", exc)
            return False

    async def today_events(self) -> list[dict]:
        if not self.is_available():
            return []
        try:
            return await asyncio.to_thread(self._today_sync)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Outlook fetch gagal: %s", exc)
            return []

    def _today_sync(self) -> list[dict]:
        token = _acquire_token_silent()
        if not token:
            return []
        now = dt.datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + dt.timedelta(days=1)
        url = (
            f"{GRAPH}/me/calendarView"
            f"?startDateTime={start.isoformat()}&endDateTime={end.isoformat()}"
            f"&$orderby=start/dateTime&$top=25"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Prefer": f'outlook.timezone="{settings.tz}"',
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        out: list[dict] = []
        for e in resp.json().get("value", []):
            when = e.get("start", {}).get("dateTime", "")
            out.append(
                {
                    "time": _fmt_time(when),
                    "title": e.get("subject", "(tanpa judul)"),
                    "source": "Outlook",
                }
            )
        return out


def _fmt_time(iso: str) -> str:
    try:
        return dt.datetime.fromisoformat(iso.split(".")[0]).strftime("%H:%M")
    except ValueError:
        return iso
