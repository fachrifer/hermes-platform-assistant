"""Central configuration for Hermes Agent.

Reads from environment variables (see .env.example). Provides a single
`settings` object used across the app so no module reaches into os.environ
directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env if present (harmless in Docker where env is injected directly).
load_dotenv()


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _get_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _resolve_existing_path(env_key: str, *fallbacks: str) -> str:
    """Prefer an existing file: configured env path first, then fallbacks."""
    candidates: list[str] = []
    configured = _get(env_key)
    if configured:
        candidates.append(configured)
    candidates.extend(path for path in fallbacks if path)
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return configured or (fallbacks[0] if fallbacks else "")


@dataclass
class Settings:
    # Core
    tz: str = field(default_factory=lambda: _get("TZ", "Asia/Jakarta"))
    port: int = field(default_factory=lambda: _get_int("HERMES_PORT", 8000))
    log_level: str = field(default_factory=lambda: _get("LOG_LEVEL", "INFO").upper())

    # Telegram
    telegram_token: str = field(default_factory=lambda: _get("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _get("TELEGRAM_CHAT_ID"))
    telegram_allowed_chat_ids: str = field(
        default_factory=lambda: _get("TELEGRAM_ALLOWED_CHAT_IDS") or _get("TELEGRAM_CHAT_ID")
    )

    # Gemini
    gemini_api_key: str = field(default_factory=lambda: _get("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _get("GEMINI_MODEL", "gemini-1.5-flash"))
    tavily_api_key: str = field(default_factory=lambda: _get("TAVILY_API_KEY"))
    # Boost general-search results from this country (Tavily country param).
    tavily_country: str = field(
        default_factory=lambda: _get("TAVILY_COUNTRY", "indonesia").casefold()
    )
    # basic | advanced — advanced ranks more strictly, uses more credits.
    tavily_search_depth: str = field(
        default_factory=lambda: _get("TAVILY_SEARCH_DEPTH", "advanced").casefold()
    )
    gemini_quota_project_id: str = field(default_factory=lambda: _get("GEMINI_QUOTA_PROJECT_ID"))
    gemini_quota_credentials: str = field(
        default_factory=lambda: _get("GEMINI_QUOTA_CREDENTIALS")
    )

    # Money Manager
    finance_account: str = field(default_factory=lambda: _get("FINANCE_ACCOUNT", "Cash"))
    finance_category_reference: str = field(
        default_factory=lambda: _resolve_existing_path(
            "FINANCE_CATEGORY_REFERENCE",
            "/app/data/reference/Kategori Money Manager.xlsx",
            str(_REPO_ROOT / "Kategori Money Manager.xlsx"),
            str(Path.cwd() / "Kategori Money Manager.xlsx"),
        )
    )
    finance_history_workbook: str = field(
        default_factory=lambda: _resolve_existing_path(
            "FINANCE_HISTORY_WORKBOOK",
            "/app/data/reference/MoneyManager-2025.xlsx",
            str(_REPO_ROOT / "MoneyManager-2025.xlsx"),
            str(Path.cwd() / "MoneyManager-2025.xlsx"),
        )
    )
    finance_export_dir: str = field(
        default_factory=lambda: _get("FINANCE_EXPORT_DIR", "/app/data/finance")
    )
    # Proven from a TSV that imports on-device: dd/MM/yyyy (e.g. 16/07/2026).
    finance_export_date_format: str = field(
        default_factory=lambda: _get("FINANCE_EXPORT_DATE_FORMAT", "%d/%m/%Y")
    )
    finance_category_recommendations: str = field(
        default_factory=lambda: _resolve_existing_path(
            "FINANCE_CATEGORY_RECOMMENDATIONS",
            "/app/data/reference/category_recommendations.json",
            str(_REPO_ROOT / "config" / "category_recommendations.json"),
            str(Path.cwd() / "config" / "category_recommendations.json"),
        )
    )

    # Google
    google_client_secrets: str = field(
        default_factory=lambda: _get("GOOGLE_CLIENT_SECRETS", "/app/credentials/google_client_secret.json")
    )
    google_token_path: str = field(
        default_factory=lambda: _get("GOOGLE_TOKEN_PATH", "/app/credentials/google_token.json")
    )
    google_calendar_write_account: str = field(
        default_factory=lambda: _get("GOOGLE_CALENDAR_WRITE_ACCOUNT", "Pribadi")
    )
    google_calendar_write: bool = field(
        default_factory=lambda: _get("GOOGLE_CALENDAR_WRITE", "0") == "1"
    )

    @property
    def google_token_paths(self) -> list[str]:
        """Return configured Google token paths, with legacy fallback."""
        configured = _get("GOOGLE_TOKEN_PATHS")
        raw_paths = configured or self.google_token_path
        return [path.strip() for path in raw_paths.split(",") if path.strip()]

    @property
    def google_account_labels(self) -> list[str]:
        """Return one display label for each configured Google account."""
        configured = [label.strip() for label in _get("GOOGLE_ACCOUNT_LABELS").split(",")]
        return [
            configured[index] if index < len(configured) and configured[index] else f"Akun {index + 1}"
            for index in range(len(self.google_token_paths))
        ]

    # Microsoft (optional, disabled by default)
    ms_outlook_enabled: bool = field(
        default_factory=lambda: _get("MS_OUTLOOK_ENABLED", "0") == "1"
    )
    ms_client_id: str = field(default_factory=lambda: _get("MS_CLIENT_ID"))
    ms_tenant_id: str = field(default_factory=lambda: _get("MS_TENANT_ID", "common"))
    ms_token_cache: str = field(
        default_factory=lambda: _get("MS_TOKEN_CACHE", "/app/credentials/ms_token_cache.json")
    )

    # Persona
    persona_name: str = field(default_factory=lambda: _get("HERMES_PERSONA", "nyx"))
    address: str = field(default_factory=lambda: _get("HERMES_ADDRESS", "Tuan"))
    persona_file: str = field(default_factory=lambda: _get("HERMES_PERSONA_FILE"))

    # Database
    db_path: str = field(default_factory=lambda: _get("HERMES_DB_PATH", "/app/data/hermes.db"))

    # Email transaction scan
    # Comma-separated list of sender addresses/domains considered financial.
    finance_email_senders: str = field(default_factory=lambda: _get("FINANCE_EMAIL_SENDERS"))
    # Comma-separated keywords that hint at a transaction.
    finance_email_keywords: str = field(
        default_factory=lambda: _get(
            "FINANCE_EMAIL_KEYWORDS",
            "transaksi,pembayaran,pembelian,struk,invoice,receipt,payment,debit,kredit,top up,topup,transfer",
        )
    )

    # Security / cloud
    hermes_env: str = field(default_factory=lambda: _get("HERMES_ENV", "development").casefold())
    disable_api_docs: bool = field(
        default_factory=lambda: _get("HERMES_DISABLE_API_DOCS", "1") == "1"
    )
    health_token: str = field(default_factory=lambda: _get("HERMES_HEALTH_TOKEN"))

    # Office observer / relay. The mTLS subject is injected only by the trusted
    # cloud reverse proxy after it verifies the laptop relay certificate.
    office_observer_id: str = field(
        default_factory=lambda: _get("OFFICE_OBSERVER_ID", "office-observer-1")
    )
    office_observer_shared_secret: str = field(
        default_factory=lambda: _get("OFFICE_OBSERVER_SHARED_SECRET")
    )
    office_observer_mtls_subject: str = field(
        default_factory=lambda: _get("OFFICE_OBSERVER_MTLS_SUBJECT")
    )

    @property
    def finance_senders_list(self) -> list[str]:
        return [s.strip() for s in self.finance_email_senders.split(",") if s.strip()]

    @property
    def finance_keywords_list(self) -> list[str]:
        return [s.strip() for s in self.finance_email_keywords.split(",") if s.strip()]

    @property
    def telegram_allowed_chat_id_set(self) -> set[str]:
        return {
            value.strip()
            for value in self.telegram_allowed_chat_ids.split(",")
            if value.strip()
        }

    def is_telegram_chat_allowed(self, chat_id: str | int | None) -> bool:
        """Return True when chat is allowlisted (or allowlist is empty in development)."""
        allowed = self.telegram_allowed_chat_id_set
        if not allowed:
            # Fail closed in production/cloud; allow open only in explicit development.
            return self.hermes_env in {"development", "dev", "local", "test"}
        return str(chat_id or "").strip() in allowed

    def ensure_dirs(self) -> None:
        """Create parent directories for data & credentials paths."""
        for p in (
            *self.google_token_paths,
            self.db_path,
            self.ms_token_cache,
            self.finance_export_dir,
            self.finance_category_reference,
            self.finance_history_workbook,
        ):
            if p:
                Path(p).parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
