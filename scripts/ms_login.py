"""One-time interactive Microsoft (Outlook/Graph) login via device code.

Run ONCE to seed the MSAL token cache that Hermes reuses silently. Place the
resulting cache in your synced credentials/ folder.

Usage:
    python -m scripts.ms_login

Requires MS_CLIENT_ID (Azure app registration, delegated Calendars.Read).
"""

from __future__ import annotations

from pathlib import Path

from msal import PublicClientApplication, SerializableTokenCache

from config.settings import settings
from connectors.outlook import SCOPES


def main() -> None:
    if not settings.ms_outlook_enabled:
        print("Outlook disabled. Set MS_OUTLOOK_ENABLED=1 to enable login.")
        return
    if not settings.ms_client_id:
        raise SystemExit("MS_CLIENT_ID belum diset (Azure app registration).")

    cache = SerializableTokenCache()
    cache_path = Path(settings.ms_token_cache)
    if cache_path.exists():
        cache.deserialize(cache_path.read_text(encoding="utf-8"))

    app = PublicClientApplication(
        settings.ms_client_id,
        authority=f"https://login.microsoftonline.com/{settings.ms_tenant_id}",
        token_cache=cache,
    )

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise SystemExit(f"Gagal memulai device flow: {flow}")
    print(flow["message"])  # instructs user to open URL and enter code

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise SystemExit(f"Login gagal: {result.get('error_description')}")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(cache.serialize(), encoding="utf-8")
    print(f"✅ Token cache tersimpan di {cache_path}")


if __name__ == "__main__":
    main()
