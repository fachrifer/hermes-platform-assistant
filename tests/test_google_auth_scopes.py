from connectors.google_auth import SCOPES
from scripts.google_login import login_scopes


def test_scopes_include_gmail_and_calendar_readonly():
    assert "https://www.googleapis.com/auth/gmail.readonly" in SCOPES
    assert "https://www.googleapis.com/auth/calendar.readonly" in SCOPES


def test_login_scopes_add_calendar_events_when_write_enabled(monkeypatch):
    monkeypatch.setattr("scripts.google_login.settings.google_calendar_write", True)
    scopes = login_scopes()
    assert "https://www.googleapis.com/auth/calendar.events" in scopes
    assert "https://www.googleapis.com/auth/gmail.readonly" in scopes
