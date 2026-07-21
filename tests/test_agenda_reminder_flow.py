import pytest

from core.agent import HermesAgent


@pytest.mark.asyncio
async def test_save_agenda_passes_requested_reminders():
    captured = {}

    class Calendar:
        def create_event(self, *args, **kwargs):
            captured.update(kwargs)
            return {"htmlLink": "https://calendar"}

    agent = HermesAgent.__new__(HermesAgent)
    agent.gcal = Calendar()
    agent.agenda = None

    await agent.save_agenda_item({
        "title": "Rapat",
        "date": "2026-07-20",
        "start_time": "10:00",
        "reminders": [{"method": "popup", "minutes": 30}],
    })

    assert captured["reminders"] == [{"method": "popup", "minutes": 30}]
