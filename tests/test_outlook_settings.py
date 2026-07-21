from config.settings import settings
from connectors.outlook import OutlookConnector


def test_outlook_is_unavailable_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ms_outlook_enabled", False)
    assert OutlookConnector().is_available() is False
