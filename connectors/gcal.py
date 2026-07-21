"""Google Calendar connector for agenda (OAuth calendar.readonly / events)."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from pathlib import Path

from googleapiclient.discovery import build

from config.settings import settings
from connectors.google_auth import GoogleAccount, load_all_credentials
from core.reminders import build_reminders_payload
from core.connector import Connector

logger = logging.getLogger("hermes.gcal")


class GoogleCalendarConnector(Connector):
    name = "Google Calendar"
    icon = "📅"

    @property
    def write_account_label(self) -> str:
        return settings.google_calendar_write_account

    def _write_service(self):
        for account, service in self._services():
            if account.label == self.write_account_label:
                return service
        raise RuntimeError(
            f"Akun Google Calendar write '{self.write_account_label}' tidak tersedia. "
            "Pastikan token akun Pribadi sudah login dengan scope Calendar write."
        )

    def create_event(
        self,
        title: str,
        start: str,
        end: str,
        location: str = "",
        notes: str = "",
        reminders: list[dict] | None = None,
    ) -> dict:
        if not settings.google_calendar_write:
            raise RuntimeError(
                "Google Calendar write belum diaktifkan. Set GOOGLE_CALENDAR_WRITE=1 "
                "setelah membuat ulang token akun Pribadi."
            )
        body = {
            "summary": title,
            "location": location or "",
            "description": notes or "",
            "start": {"dateTime": start, "timeZone": settings.tz},
            "end": {"dateTime": end, "timeZone": settings.tz},
        }
        reminder_payload = build_reminders_payload(reminders)
        if reminder_payload:
            body["reminders"] = reminder_payload
        try:
            return self._write_service().events().insert(calendarId="primary", body=body).execute()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Gagal menulis Google Calendar akun Pribadi. Periksa scope, Calendar API, dan token."
            ) from exc

    def query_events(self, start: dt.datetime, end: dt.datetime) -> list[dict]:
        """Read events only from the configured personal Calendar account."""
        try:
            response = self._write_service().events().list(
                calendarId="primary",
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            ).execute()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Google Calendar Pribadi tidak bisa dibaca. Periksa token, scope, dan Calendar API."
            ) from exc
        rows = []
        for event in response.get("items", []):
            start_value = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date", "")
            end_value = event.get("end", {}).get("dateTime") or event.get("end", {}).get("date", "")
            rows.append({
                "id": event.get("id", ""),
                "title": event.get("summary", "(tanpa judul)"),
                "start": start_value,
                "end": end_value,
                "location": event.get("location", ""),
                "source": "Google Calendar (Pribadi)",
                "url": event.get("htmlLink", ""),
            })
        return rows

    def update_event_reminders(self, event_id: str, reminders: list[dict]) -> dict:
        if not settings.google_calendar_write:
            raise RuntimeError(
                "Google Calendar write belum diaktifkan. Set GOOGLE_CALENDAR_WRITE=1 "
                "setelah membuat ulang token akun Pribadi."
            )
        body = {
            "reminders": {
                "useDefault": False,
                "overrides": reminders,
            }
        }
        try:
            return self._write_service().events().patch(
                calendarId="primary", eventId=event_id, body=body
            ).execute()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Gagal mengubah reminder Google Calendar Pribadi. Periksa token, scope, dan Calendar API."
            ) from exc

    def is_available(self) -> bool:
        return any(Path(path).exists() for path in settings.google_token_paths)

    def _services(self) -> list[tuple[GoogleAccount, object]]:
        services = []
        for account in load_all_credentials():
            try:
                services.append(
                    (account, build("calendar", "v3", credentials=account.credentials, cache_discovery=False))
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Gagal membuat service Calendar akun %s: %s", account.label, exc)
        return services

    async def health_check(self) -> bool:
        if not self.is_available():
            return False
        try:
            return await asyncio.to_thread(self._check_sync)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Google Calendar health check gagal: %s", exc)
            return False

    def _check_sync(self) -> bool:
        for account, svc in self._services():
            try:
                svc.calendarList().list(maxResults=1).execute()
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Google Calendar akun %s gagal: %s", account.label, exc)
        return False

    async def today_events(self) -> list[dict]:
        """Return today's events as [{time, title, source}]."""
        if not self.is_available():
            return []
        try:
            return await asyncio.to_thread(self._today_sync)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Google Calendar fetch gagal: %s", exc)
            return []

    def _today_sync(self) -> list[dict]:
        now = dt.datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + dt.timedelta(days=1)
        out: list[dict] = []
        for account, svc in self._services():
            try:
                events = svc.events().list(
                    calendarId="primary",
                    timeMin=start.isoformat(),
                    timeMax=end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                ).execute()
                for e in events.get("items", []):
                    s = e.get("start", {})
                    when = s.get("dateTime", s.get("date", ""))
                    out.append({
                        "time": _fmt_time(when),
                        "title": e.get("summary", "(tanpa judul)"),
                        "source": "GCal",
                        "account": account.label,
                    })
            except Exception as exc:  # noqa: BLE001
                logger.warning("Google Calendar akun %s gagal: %s", account.label, exc)
        return out


def _fmt_time(iso: str) -> str:
    try:
        if "T" in iso:
            return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%H:%M")
        return "seharian"
    except ValueError:
        return iso
