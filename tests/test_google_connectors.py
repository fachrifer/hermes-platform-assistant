from types import SimpleNamespace

import pytest

import connectors.gcal as gcal_module
from connectors.google_auth import GoogleAccount
from connectors.gcal import GoogleCalendarConnector


class FakeCalendarService:
    def __init__(self, account_label: str):
        self.account_label = account_label

    def calendarList(self):
        return SimpleNamespace(list=lambda **kwargs: SimpleNamespace(execute=lambda: {"items": []}))

    def events(self):
        return self

    def list(self, **kwargs):
        return SimpleNamespace(
            execute=lambda: {
                "items": [
                    {
                        "summary": f"Event {self.account_label}",
                        "start": {"dateTime": "2026-07-16T09:00:00+07:00"},
                    }
                ]
            }
        )


@pytest.mark.asyncio
async def test_calendar_health_with_one_account(monkeypatch):
    accounts = [GoogleAccount("Pribadi", gcal_module.Path("/one.json"), object())]
    monkeypatch.setattr(gcal_module.Path, "exists", lambda _path: True)
    monkeypatch.setattr(gcal_module, "load_all_credentials", lambda: accounts)
    monkeypatch.setattr(
        gcal_module,
        "build",
        lambda *args, **kwargs: FakeCalendarService("Pribadi"),
    )

    connector = GoogleCalendarConnector()
    assert await connector.health_check() is True
    events = await connector.today_events()
    assert events[0]["account"] == "Pribadi"


@pytest.mark.asyncio
async def test_calendar_skips_failed_account(monkeypatch):
    accounts = [
        GoogleAccount("Pribadi", gcal_module.Path("/one.json"), object()),
        GoogleAccount("Kerja", gcal_module.Path("/two.json"), object()),
    ]
    monkeypatch.setattr(gcal_module.Path, "exists", lambda _path: True)
    monkeypatch.setattr(gcal_module, "load_all_credentials", lambda: accounts)

    def build_service(_api, _version, **kwargs):
        if kwargs["credentials"] is accounts[0].credentials:
            raise RuntimeError("account unavailable")
        return FakeCalendarService("Kerja")

    monkeypatch.setattr(gcal_module, "build", build_service)
    events = await GoogleCalendarConnector().today_events()
    assert [e["account"] for e in events] == ["Kerja"]
