"""One-time interactive Google OAuth login (Gmail + Calendar).

Run this ONCE on your machine (outside the container) to produce the cached token
that Hermes reuses for Gmail finance import and Google Calendar. Place the resulting
token in your synced credentials/ folder.

Usage:
    python -m scripts.google_login

Requires GOOGLE_CLIENT_SECRETS (OAuth client json) and writes GOOGLE_TOKEN_PATH.
"""

from __future__ import annotations

from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from config.settings import settings
from connectors.google_auth import SCOPES


def login_scopes() -> list[str]:
    if settings.google_calendar_write:
        return [*SCOPES, "https://www.googleapis.com/auth/calendar.events"]
    return list(SCOPES)


def main() -> None:
    secrets = Path(settings.google_client_secrets)
    if not secrets.exists():
        raise SystemExit(
            f"File OAuth client tidak ditemukan: {secrets}\n"
            "Unduh dari Google Cloud Console (OAuth client, Desktop app) dan set "
            "GOOGLE_CLIENT_SECRETS. Aktifkan Gmail API dan Google Calendar API."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), login_scopes())
    creds = flow.run_local_server(port=0)
    token_path = Path(settings.google_token_path)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"✅ Token Calendar tersimpan di {token_path}")


if __name__ == "__main__":
    main()
