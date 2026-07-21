from config.settings import Settings


def test_telegram_allowlist_defaults_to_chat_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setenv("HERMES_ENV", "production")
    settings = Settings()
    assert settings.is_telegram_chat_allowed("111")
    assert not settings.is_telegram_chat_allowed("999")


def test_telegram_allowlist_fail_closed_in_production_without_ids(monkeypatch):
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setenv("HERMES_ENV", "production")
    settings = Settings()
    assert not settings.is_telegram_chat_allowed("123")


def test_telegram_allowlist_open_in_development_without_ids(monkeypatch):
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setenv("HERMES_ENV", "development")
    settings = Settings()
    assert settings.is_telegram_chat_allowed("123")
