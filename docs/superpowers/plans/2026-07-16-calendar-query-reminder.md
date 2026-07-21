# Google Calendar Query and Reminder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make natural-language Calendar queries read the personal account and make event reminders follow the user's explicit request with no default reminder.

**Architecture:** Add a small reminder parser/normalizer and pass its output through the existing agenda save path into Google Calendar `events.insert`. Extend the intent router with `calendar_query`, add a personal-account Calendar range query, and keep the work account out of all Calendar operations.

**Tech Stack:** Python 3.11, Google Calendar API v3, Gemini intent router, SQLite fallback, pytest.

## Global Constraints

- Read and write Calendar only through the `Pribadi` account.
- The `Kerja` account remains Gmail read-only.
- Events have no reminder by default.
- A reminder request without a method uses Google Calendar `popup`.
- `email` is used only when the user explicitly requests email notification.
- Multiple requested reminders are preserved.
- Missing read scope, disabled API, expired token, and network errors produce actionable messages.

---

### Task 1: Reminder Parser and Event Payload

**Files:**
- Create: `core/reminders.py`
- Modify: `connectors/gcal.py` event creation payload
- Modify: `core/agent.py` agenda save path
- Test: `tests/test_reminders.py`

**Interfaces:**
- `parse_reminders(text: str) -> list[dict]` returns `[{"method": "popup"|"email", "minutes": int}]`.
- `build_reminders_payload(reminders: list[dict] | None) -> dict | None`.
- `GoogleCalendarConnector.create_event(title: str, start: str, end: str, location: str, notes: str, reminders: list[dict] | None = None) -> dict`.

- [ ] **Step 1: Write failing reminder tests**

```python
from core.reminders import build_reminders_payload, parse_reminders


def test_no_reminder_request_returns_empty():
    assert parse_reminders("Rapat dengan client") == []
    assert build_reminders_payload([]) is None


def test_unspecified_method_defaults_to_popup():
    assert parse_reminders("ingatkan 30 menit sebelumnya") == [
        {"method": "popup", "minutes": 30}
    ]


def test_explicit_email_and_multiple_reminders():
    assert parse_reminders("ingatkan 1 jam sebelumnya lewat email dan 15 menit sebelumnya popup") == [
        {"method": "email", "minutes": 60},
        {"method": "popup", "minutes": 15},
    ]
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `PYTHONPATH=. pytest tests/test_reminders.py -q`

Expected: collection failure because `core.reminders` does not exist.

- [ ] **Step 3: Implement reminder parsing**

Recognize minutes, hours, and days in Indonesian and English. Associate an explicit `email`/`email notification` phrase with the nearest duration; otherwise use `popup`. Return one entry per requested reminder, preserving order and removing exact duplicates.

- [ ] **Step 4: Implement payload omission**

Return `None` for no reminders. Otherwise return:

```python
{
    "useDefault": False,
    "overrides": reminders,
}
```

Add the `reminders` key to the Calendar event body only when the payload is not `None`.

- [ ] **Step 5: Run reminder tests**

Run: `PYTHONPATH=. pytest tests/test_reminders.py -q`

Expected: PASS.

### Task 2: Personal Calendar Query

**Files:**
- Modify: `connectors/gcal.py`
- Modify: `core/agent.py` `_TOOLS_DESC` and message handling
- Create: `tests/test_calendar_query.py`

**Interfaces:**
- `GoogleCalendarConnector.query_events(start: datetime, end: datetime) -> list[dict]`.
- `HermesAgent._do_calendar_query(question: str, start: datetime, end: datetime) -> str`.

- [ ] **Step 1: Write failing query tests**

```python
import pytest

from core.agent import HermesAgent

from connectors.gcal import GoogleCalendarConnector


def test_query_events_uses_only_write_account(monkeypatch):
    connector = GoogleCalendarConnector()
    monkeypatch.setattr(connector, "_write_service", lambda: None)
    monkeypatch.setattr(connector, "_services", lambda: [])

    assert connector.query_events(None, None) == []
```

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `PYTHONPATH=. pytest tests/test_calendar_query.py -q`

Expected: FAIL because `query_events` is absent.

- [ ] **Step 3: Implement personal-account range query**

Use only the service selected by `settings.google_calendar_write_account`, call `events().list(calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(), singleEvents=True, orderBy="startTime")`, and normalize title, start, end, location, and `source="Google Calendar (Pribadi)"`. Return an actionable error for missing personal account or Google API failures.

- [ ] **Step 4: Add the `calendar_query` intent**

Add:

```text
- calendar_query: user asks to read agenda/calendar. args: {"question": string, "range": "today"|"tomorrow"|"week"}
```

Route it to a date-range helper using the configured timezone and format the answer directly from Calendar results. Empty results must say there are no events, not that access is unavailable.

- [ ] **Step 5: Run query tests**

Run: `PYTHONPATH=. pytest tests/test_calendar_query.py -q`

Expected: PASS.

### Task 3: Agenda Creation With User Reminders

**Files:**
- Modify: `core/agenda_extraction.py` reminder fields
- Modify: `core/agent.py` `save_agenda_item`
- Modify: `core/telegram_bot.py` `/agenda tambah` and confirmation preview
- Test: `tests/test_agenda_reminder_flow.py`

**Interfaces:**
- Agenda item dictionaries may contain `reminders`.
- `HermesAgent.save_agenda_item(item)` passes normalized reminders to `gcal.create_event`.

- [ ] **Step 1: Write failing save-flow tests**

```python
import pytest


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
```

- [ ] **Step 2: Run focused tests and confirm they fail because reminders are not passed**

Run: `PYTHONPATH=. pytest tests/test_agenda_reminder_flow.py -q`

Expected: FAIL because the current save path does not pass `reminders`.

- [ ] **Step 3: Carry reminder data through extraction and text commands**

Add `reminders` to normalized agenda items. Parse reminder phrases from `/agenda tambah` text and from natural-language agenda requests before saving. Keep the list empty when no request exists.

- [ ] **Step 4: Show reminders in file confirmation preview**

Display `Reminder: popup 30 menit sebelumnya` or `Reminder: email 1 jam sebelumnya` only when reminders are present. The confirmation callback passes the reminder list into `save_agenda_item`.

- [ ] **Step 5: Pass reminders to Calendar and keep fallback behavior**

Call `create_event(item["title"], start, end, item.get("location", ""), item.get("notes", ""), reminders=item.get("reminders"))`. Local SQLite fallback stores the reminder description in notes so the requested intent is not lost when Calendar is unavailable.

- [ ] **Step 6: Run save-flow tests**

Run: `PYTHONPATH=. pytest tests/test_agenda_reminder_flow.py -q`

Expected: PASS.

### Task 4: Documentation and Full Verification

**Files:**
- Modify: `README.md` Calendar query, agenda, and reminder sections
- Test: full suite

- [ ] **Step 1: Document natural-language Calendar queries**

Add examples for `cek agenda saya di Google Calendar`, `ada agenda besok?`, and `jadwal saya minggu ini apa?`. State that queries use account `Pribadi` and account `Kerja` is not used for Calendar.

- [ ] **Step 2: Document reminder behavior**

Explain no default reminder, popup for unspecified reminder method, explicit email only, and multiple reminder examples.

- [ ] **Step 3: Run all tests**

Run: `PYTHONPATH=. pytest -q`

Expected: all tests pass with exit code 0.

- [ ] **Step 4: Rebuild and verify the container**

Run: `docker compose up --build -d`

Run: `docker compose ps`

Expected: `hermes` reports `healthy`.
