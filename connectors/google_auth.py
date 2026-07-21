"""Shared Google OAuth helpers for Gmail + Calendar."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from config.settings import settings

logger = logging.getLogger("hermes.google_auth")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


@dataclass(frozen=True)
class GoogleAccount:
    label: str
    token_path: Path
    credentials: Credentials


def load_all_credentials() -> list[GoogleAccount]:
    """Load every usable configured Google account independently."""
    accounts: list[GoogleAccount] = []
    for label, raw_path in zip(settings.google_account_labels, settings.google_token_paths):
        token_path = Path(raw_path)
        if not token_path.exists():
            logger.info("Token Google akun %s belum ditemukan: %s", label, token_path)
            continue
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token_path.write_text(creds.to_json(), encoding="utf-8")
            if creds and creds.valid:
                accounts.append(GoogleAccount(label, token_path, creds))
            else:
                logger.warning("Token Google akun %s tidak valid", label)
        except Exception:  # noqa: BLE001
            logger.exception("Gagal memuat token Google akun %s", label)
    return accounts
