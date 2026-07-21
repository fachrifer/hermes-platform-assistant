def _agent_with_tmp_settings(monkeypatch, tmp_path):
    db_path = tmp_path / "hermes.db"
    export_dir = tmp_path / "finance"
    creds_dir = tmp_path / "credentials"
    creds_dir.mkdir()
    token_path = creds_dir / "google_token.json"
    token_path.write_text("{}")

    monkeypatch.setenv("HERMES_DB_PATH", str(db_path))
    monkeypatch.setenv("FINANCE_EXPORT_DIR", str(export_dir))
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(token_path))
    monkeypatch.setenv("GOOGLE_TOKEN_PATHS", str(token_path))
    monkeypatch.setenv("GOOGLE_CLIENT_SECRETS", str(creds_dir / "google_client_secret.json"))
    monkeypatch.setenv("MS_TOKEN_CACHE", str(creds_dir / "ms_token_cache.json"))
    monkeypatch.setenv("MS_OUTLOOK_ENABLED", "0")
    monkeypatch.setattr("config.settings.load_dotenv", lambda *args, **kwargs: None)

    import importlib

    import config.settings as settings_mod
    import core.db as db_mod

    importlib.reload(settings_mod)
    importlib.reload(db_mod)

    import core.agent as agent_mod

    importlib.reload(agent_mod)

    return agent_mod.HermesAgent()


def test_agent_registers_gmail_connector(monkeypatch, tmp_path):
    agent = _agent_with_tmp_settings(monkeypatch, tmp_path)

    assert any(c.name == "Gmail" for c in agent.connectors)
    assert agent.finance_import.gmail is agent.gmail

    gmail_index = next(i for i, c in enumerate(agent.connectors) if c.name == "Gmail")
    gcal_index = next(i for i, c in enumerate(agent.connectors) if c.name == "Google Calendar")
    assert gmail_index < gcal_index
