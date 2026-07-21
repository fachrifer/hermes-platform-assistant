"""Multi-account Gmail connector for finance scan (readonly)."""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import html as html_lib
import logging
import re
from email.utils import parsedate_to_datetime

from googleapiclient.discovery import build

from config.settings import settings
from connectors.google_auth import load_all_credentials
from core.connector import Connector

logger = logging.getLogger("hermes.gmail")


def _decode_base64url(data: str) -> str:
    if not data:
        return ""
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")


def _html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


class GmailConnector(Connector):
    name = "Gmail"
    icon = "📧"

    def is_available(self) -> bool:
        from pathlib import Path

        return any(Path(path).exists() for path in settings.google_token_paths)

    def _build_finance_range_query(self, start: dt.date, end: dt.date) -> str:
        after = (start - dt.timedelta(days=1)).strftime("%Y/%m/%d")
        before = (end + dt.timedelta(days=1)).strftime("%Y/%m/%d")
        parts = [f"after:{after}", f"before:{before}"]
        senders = settings.finance_senders_list
        if senders:
            parts.append("from:(" + " OR ".join(senders) + ")")
        keywords = settings.finance_keywords_list
        if keywords:
            parts.append("(" + " OR ".join(f'"{k}"' for k in keywords) + ")")
        return " ".join(parts)

    def _service(self, credentials):
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def _extract_body(self, payload: dict) -> str:
        def walk(part: dict) -> tuple[str | None, str | None]:
            mime = part.get("mimeType", "")
            data = part.get("body", {}).get("data", "")

            if mime == "text/plain" and data:
                return _decode_base64url(data), None
            if mime == "text/html" and data:
                return None, _decode_base64url(data)
            if mime.startswith("multipart/"):
                plain: str | None = None
                html: str | None = None
                for sub in part.get("parts", []):
                    sub_plain, sub_html = walk(sub)
                    if sub_plain and not plain:
                        plain = sub_plain
                    if sub_html and not html:
                        html = sub_html
                return plain, html
            return None, None

        plain, html = walk(payload)
        if plain:
            return plain.strip()
        if html:
            return _html_to_text(html)
        return ""

    def _scan_account_range(self, account, start, end, max_results) -> list[dict]:
        service = self._service(account.credentials)
        query = self._build_finance_range_query(start, end)
        rows: list[dict] = []
        page_token: str | None = None

        while len(rows) < max_results:
            list_kwargs: dict = {
                "userId": "me",
                "q": query,
                "maxResults": min(max_results - len(rows), 100),
            }
            if page_token:
                list_kwargs["pageToken"] = page_token

            response = service.users().messages().list(**list_kwargs).execute()
            messages = response.get("messages", [])
            if not messages:
                break

            for msg_ref in messages:
                if len(rows) >= max_results:
                    break
                msg_id = msg_ref["id"]
                try:
                    full = service.users().messages().get(
                        userId="me", id=msg_id, format="full"
                    ).execute()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Gmail get message %s akun %s gagal: %s",
                        msg_id,
                        account.label,
                        exc,
                    )
                    continue

                headers = {
                    h["name"].lower(): h["value"]
                    for h in full.get("payload", {}).get("headers", [])
                }
                date_hdr = headers.get("date", "")
                date_str = ""
                if date_hdr:
                    try:
                        date_str = parsedate_to_datetime(date_hdr).date().isoformat()
                    except (TypeError, ValueError, OverflowError):
                        date_str = date_hdr

                rows.append({
                    "id": f"{account.label}:{msg_id}",
                    "account": account.label,
                    "from": headers.get("from", ""),
                    "subject": headers.get("subject", ""),
                    "date": date_str,
                    "body": self._extract_body(full.get("payload", {})),
                })

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return rows

    def scan_finance_range(self, start: dt.date, end: dt.date, max_results: int = 100) -> list[dict]:
        rows: list[dict] = []
        for account in load_all_credentials():
            try:
                rows.extend(self._scan_account_range(account, start, end, max_results))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Gmail scan akun %s gagal: %s", account.label, exc)
        return rows

    async def health_check(self) -> bool:
        if not self.is_available():
            return False
        return await asyncio.to_thread(self._check_sync)

    def _check_sync(self) -> bool:
        for account in load_all_credentials():
            try:
                self._service(account.credentials).users().getProfile(userId="me").execute()
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Gmail health akun %s gagal: %s", account.label, exc)
        return False
