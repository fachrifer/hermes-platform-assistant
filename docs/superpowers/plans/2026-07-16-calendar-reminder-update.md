# Calendar Reminder Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix natural-language Calendar reads and add one-by-one confirmation when changing reminders on existing personal Calendar events.

**Architecture:** Add deterministic Calendar intent detection before Gemini fallback, retain event IDs in Calendar query results, and add `events.update` with reminder-only changes. Telegram stores matching events in a pending queue and confirms or skips each event individually.

**Tech Stack:** Python 3.11, Google Calendar API v3, python-telegram-bot, Gemini intent routing, pytest.

## Global Constraints

- Read and write Calendar only through the `Pribadi` account.
- The `Kerja` account remains Gmail read-only.
- Only confirmed events may be updated.
- If no event matches, no Calendar mutation occurs.
- Every update reports the actual API result; no success claim before `events.update` succeeds.

---

### Task 1: Calendar Event IDs and Reminder Updates

**Files:**
- Modify: `connectors/gcal.py`
- Test: `tests/test_calendar_query.py`

**Interfaces:**
- `query_events(...)` includes `id` in every normalized event.
- `update_event_reminders(event_id: str, reminders: list[dict]) -> dict`.

- [ ] **Step 1: Write failing update test**

```python
def test_update_event_reminders_uses_personal_calendar(monkeypatch):
    connector = GoogleCalendarConnector()
    captured = {}
    monkeypatch.setattr("connectors.gcal.settings.google_calendar_write", True)

    class Events:
        def update(self, **kwargs):
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
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `PYTHONPATH=. pytest tests/test_calendar_query.py -q`

Expected: FAIL because `update_event_reminders` is absent and query rows lack event IDs.

- [ ] **Step 3: Add event IDs and update implementation**

Include `event.get("id")` in `query_events` output. Implement `events().update(calendarId="primary", eventId=event_id, body={"reminders": {"useDefault": False, "overrides": reminders}})`. Wrap Google errors in an actionable RuntimeError.

- [ ] **Step 4: Run focused tests**

Run: `PYTHONPATH=. pytest tests/test_calendar_query.py -q`

Expected: PASS.

### Task 2: Deterministic Calendar Query and Update Intents

**Files:**
- Modify: `core/agent.py`
- Test: `tests/test_calendar_intents.py`

**Interfaces:**
- `HermesAgent.plan_message` returns `calendar_query` for clear Calendar-read phrases before Gemini classification.
- `HermesAgent.plan_message` returns `calendar_update_reminders` for clear reminder-update phrases.

- [ ] **Step 1: Write failing intent tests**

```python
import pytest

from core.agent import HermesAgent


@pytest.mark.asyncio
async def test_calendar_query_does_not_depend_on_gemini_classifier():
    agent = HermesAgent.__new__(HermesAgent)
    agent.llm = type("LLM", (), {"plan": lambda *args: (_ for _ in ()).throw(AssertionError())})()

    result = await agent.plan_message("cek agenda saya di Google Calendar")

    assert result["action"] == "calendar_query"


@pytest.mark.asyncio
async def test_reminder_update_intent_extracts_requested_minutes():
    agent = HermesAgent.__new__(HermesAgent)
    agent.llm = type("LLM", (), {"plan": lambda *args: (_ for _ in ()).throw(AssertionError())})()

    result = await agent.plan_message("ubah reminder Kereta Parahyangan 134B menjadi 3 jam sebelumnya")

    assert result["action"] == "calendar_update_reminders"
    assert result["args"]["minutes"] == 180
```

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `PYTHONPATH=. pytest tests/test_calendar_intents.py -q`

Expected: FAIL because `plan_message` currently delegates these requests to Gemini.

- [ ] **Step 3: Add deterministic intent detection**

Detect Calendar-read phrases containing `agenda`, `jadwal`, or `calendar` plus `cek`, `lihat`, `ada`, or `minggu`. Detect update phrases containing `reminder`/`pengingat` and `ubah`/`sesuaikan`/`menjadi`. Parse `minutes` from minutes/hours/days and preserve the target title after removing command words.

- [ ] **Step 4: Route calendar reminder updates**

Add `calendar_update_reminders` to `_TOOLS_DESC`. In `handle_message`, query the personal Calendar for the relevant range, filter matching event titles case-insensitively, and enqueue matches for Telegram confirmation instead of mutating immediately.

- [ ] **Step 5: Run intent tests**

Run: `PYTHONPATH=. pytest tests/test_calendar_intents.py -q`

Expected: PASS.

### Task 3: Telegram One-by-One Confirmation

**Files:**
- Modify: `core/telegram_bot.py`
- Modify: `core/agent.py` update delegation
- Test: `tests/test_calendar_reminder_confirmation.py`

**Interfaces:**
- Pending state stores `event_id`, `title`, proposed `reminders`, and remaining matches.
- Callback data uses `calendar_reminder_confirm:<token>` and `calendar_reminder_skip:<token>`.

- [ ] **Step 1: Write failing callback registration test**

```python
def test_calendar_reminder_callback_handler_exists():
    from core.telegram_bot import TelegramInterface

    assert hasattr(TelegramInterface, "_on_calendar_reminder_callback")
```

- [ ] **Step 2: Run test and confirm it fails**

Run: `PYTHONPATH=. pytest tests/test_calendar_reminder_confirmation.py -q`

Expected: FAIL because the callback does not exist.

- [ ] **Step 3: Implement one-by-one preview**

For each matching event, show title, date/time, current reminder state, and proposed reminder. Buttons must be **Konfirmasi** and **Lewati**. If no event matches, reply that no changes were made.

- [ ] **Step 4: Implement confirm/skip callbacks**

On confirm, call `agent.gcal.update_event_reminders(event_id, reminders)` and only then report success. On skip, report skipped and show the next event. After the queue is empty, report counts confirmed/skipped.

- [ ] **Step 5: Run confirmation tests**

Run: `PYTHONPATH=. pytest tests/test_calendar_reminder_confirmation.py -q`

Expected: PASS.

### Task 4: Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Test: full suite

- [ ] **Step 1: Document Calendar query and reminder update examples**

Add examples for reading Calendar and changing reminders, including the one-by-one confirmation behavior and the fact that no update occurs before confirmation.

- [ ] **Step 2: Run all tests**

Run: `PYTHONPATH=. pytest -q`

Expected: all tests pass with exit code 0.

- [ ] **Step 3: Rebuild and verify the container**

Run: `docker compose up --build -d`

Run: `docker compose ps`

Expected: `hermes` reports `healthy`.
