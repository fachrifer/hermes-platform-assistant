from connectors.gcal import GoogleCalendarConnector


def test_write_account_is_configured_label(monkeypatch):
    monkeypatch.setattr("connectors.gcal.settings.google_calendar_write_account", "Pribadi")
    connector = GoogleCalendarConnector()

    assert connector.write_account_label == "Pribadi"


def test_event_payload_uses_primary_calendar(monkeypatch):
    connector = GoogleCalendarConnector()
    captured = {}
    monkeypatch.setattr("connectors.gcal.settings.google_calendar_write", True)

    class Events:
        def insert(self, **kwargs):
            captured.update(kwargs)
            return type("Request", (), {"execute": lambda self: {"id": "event-1", "htmlLink": "https://calendar"}})()

    class Service:
        def events(self):
            return Events()

    monkeypatch.setattr(connector, "_write_service", lambda: Service())

    result = connector.create_event(
        "Rapat", "2026-07-20T10:00:00+07:00", "2026-07-20T11:00:00+07:00", "Jakarta", "Catatan"
    )

    assert result["id"] == "event-1"
    assert captured["calendarId"] == "primary"
    assert captured["body"]["summary"] == "Rapat"


def test_event_payload_omits_reminders_when_not_requested(monkeypatch):
    connector = GoogleCalendarConnector()
    monkeypatch.setattr("connectors.gcal.settings.google_calendar_write", True)
    captured = {}

    class Service:
        def events(self):
            return type("Events", (), {
                "insert": lambda self, **kwargs: (
                    captured.update(kwargs)
                    or type("Request", (), {"execute": lambda self: {}})()
                )
            })()

    monkeypatch.setattr(connector, "_write_service", lambda: Service())
    connector.create_event("Rapat", "2026-07-20T10:00:00+07:00", "2026-07-20T11:00:00+07:00")

    assert "reminders" not in captured["body"]
