import pytest

from config.persona import display_name
from core.briefing import BriefingService


class FakeAgent:
    def __init__(self):
        self.spreadsheet_finance = self

    async def health_check_all(self):
        return {"Google Calendar": True, "Agenda Manual": True}

    async def all_today_agenda(self):
        return []

    def month_summary(self, period):
        return {"rows": [], "income_total": 0, "expense_total": 0}


@pytest.mark.asyncio
async def test_brief_is_nyx_not_squire(monkeypatch):
    monkeypatch.setattr("core.briefing.settings.address", "Master")
    monkeypatch.setattr("core.briefing.display_name", lambda: "Nyx Assistant")
    text = await BriefingService(FakeAgent()).build()
    assert "Nyx Assistant" in text
    assert "Master" in text
    assert "Surat Masuk" not in text
    assert "Gmail" not in text
    for marker in ("squire", "titah", "kulaksanakan", "dengan segala hormat", "siap sedia"):
        assert marker not in text.casefold()


@pytest.mark.asyncio
async def test_startup_brief_avoids_squire(monkeypatch):
    monkeypatch.setattr("core.briefing.settings.address", "Master")
    monkeypatch.setattr("core.briefing.display_name", lambda: "Nyx Assistant")
    text = await BriefingService(FakeAgent()).startup_brief({"Google Calendar": True})
    assert "Nyx Assistant" in text
    assert "siap sedia" not in text.casefold()
    assert "squire" not in text.casefold()
