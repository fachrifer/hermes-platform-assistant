import pytest

from core.agenda_extraction import normalize_agenda_items, parse_agenda_text
from core.llm_client import LLMClient


def test_normalize_agenda_items_keeps_all_items():
    items = normalize_agenda_items({
        "agendas": [
            {"title": "Rapat", "date": "2026-07-20"},
            {"title": "Makan"},
        ]
    })

    assert len(items) == 2
    assert items[0]["title"] == "Rapat"
    assert items[1]["needs_review"] is True


def test_normalize_rejects_empty_titles():
    assert normalize_agenda_items({"agendas": [{"title": ""}]}) == []


def test_parse_agenda_text_extracts_explicit_date_and_time():
    item = parse_agenda_text("2026-07-20 10:00 Rapat dengan Client A")

    assert item == {
        "title": "Rapat dengan Client A",
        "date": "2026-07-20",
        "start_time": "10:00",
        "end_time": "",
        "location": "",
        "notes": "",
        "reminders": [],
        "needs_review": False,
    }


@pytest.mark.asyncio
async def test_llm_extracts_agendas_from_attachment_bytes():
    class Model:
        def generate_content(self, contents):
            assert contents[1]["mime_type"] == "application/pdf"
            return type("Response", (), {
                "text": '{"agendas":[{"title":"Rapat","date":"2026-07-20","start_time":"10:00"}]}'
            })()

    client = LLMClient.__new__(LLMClient)
    client.enabled = True
    client._model = Model()

    items = await client.extract_agenda_attachment(b"pdf", "application/pdf")

    assert items[0]["title"] == "Rapat"
