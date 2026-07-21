import pytest

from core.agent import HermesAgent


@pytest.mark.asyncio
async def test_calendar_query_does_not_depend_on_gemini_classifier():
    agent = HermesAgent.__new__(HermesAgent)

    class LLM:
        async def plan(self, *args):
            raise AssertionError("Calendar query harus terdeteksi sebelum Gemini")

    agent.llm = LLM()

    result = await agent.plan_message("cek agenda saya di Google Calendar")

    assert result["action"] == "calendar_query"


@pytest.mark.asyncio
async def test_reminder_update_intent_extracts_requested_minutes():
    agent = HermesAgent.__new__(HermesAgent)

    class LLM:
        async def plan(self, *args):
            raise AssertionError("Reminder update harus terdeteksi sebelum Gemini")

    agent.llm = LLM()

    result = await agent.plan_message(
        "ubah reminder Kereta Parahyangan 134B menjadi 3 jam sebelumnya"
    )

    assert result["action"] == "calendar_update_reminders"
    assert result["args"]["minutes"] == 180


@pytest.mark.asyncio
async def test_reminder_update_strips_trip_prefix():
    agent = HermesAgent.__new__(HermesAgent)
    agent.llm = type("LLM", (), {"plan": lambda *args: None})()

    result = await agent.plan_message(
        "sesuaikan seluruh agenda perjalanan Kereta Parahyangan 134B & 139B menjadi 3 jam sebelumnya"
    )

    assert result["args"]["title"] == "Kereta Parahyangan 134B & 139B"


@pytest.mark.asyncio
async def test_agenda_id_update_is_parsed_naturally():
    agent = HermesAgent.__new__(HermesAgent)
    agent.llm = type("LLM", (), {"plan": lambda *args: None})()

    result = await agent.plan_message("agenda 1 update reminder 3 jam sebelum perjalanan")

    assert result["action"] == "calendar_update_reminders"
    assert result["args"]["agenda_id"] == 1
    assert result["args"]["minutes"] == 180
