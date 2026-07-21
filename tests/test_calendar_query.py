import datetime as dt

from connectors.gcal import GoogleCalendarConnector


def test_query_events_reads_only_selected_personal_calendar(monkeypatch):
    connector = GoogleCalendarConnector()

    class Events:
        def list(self, **kwargs):
            assert kwargs["calendarId"] == "primary"
            return type("Request", (), {
                "execute": lambda self: {"items": [{
                    "summary": "Rapat",
                    "location": "Jakarta",
                    "start": {"dateTime": "2026-07-20T10:00:00+07:00"},
                    "end": {"dateTime": "2026-07-20T11:00:00+07:00"},
                }]}
            })()

    class Service:
        def events(self):
            return Events()

    monkeypatch.setattr(connector, "_write_service", lambda: Service())

    rows = connector.query_events(
        dt.datetime(2026, 7, 20, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 7, 21, tzinfo=dt.timezone.utc),
    )

    assert rows[0]["title"] == "Rapat"
    assert rows[0]["source"] == "Google Calendar (Pribadi)"


def test_update_event_reminders_uses_personal_calendar(monkeypatch):
    connector = GoogleCalendarConnector()
    captured = {}
    monkeypatch.setattr("connectors.gcal.settings.google_calendar_write", True)

    class Events:
        def patch(self, **kwargs):
            captured.update(kwargs)
            return type("Request", (), {"execute": lambda self: {"id": "event-1"}})()

    class Service:
        def events(self):
            return Events()

    monkeypatch.setattr(connector, "_write_service", lambda: Service())

    connector.update_event_reminders("event-1", [{"method": "popup", "minutes": 180}])

    assert captured["calendarId"] == "primary"
    assert captured["eventId"] == "event-1"
    assert captured["body"]["reminders"]["overrides"][0]["minutes"] == 180
