# Agenda File and Google Calendar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read agenda items from Telegram image/PDF attachments, confirm them one by one, and write timed events only to the personal Google Calendar with a local fallback.

**Architecture:** Extend Google Calendar with an explicit write-account selector and `events.insert`, while keeping the work account read-only. Add a multimodal extraction method to `LLMClient`, a pending agenda confirmation map in `TelegramInterface`, and a single agenda-save service path that verifies local writes and falls back when Calendar is unavailable.

**Tech Stack:** Python 3.11, python-telegram-bot 21.9, Gemini `google-generativeai`, Google Calendar API v3, SQLite, pytest.

## Global Constraints

- Google account `Pribadi` is the only account allowed to write Calendar events.
- Google account `Kerja` remains Gmail read-only and is never used for Calendar writes.
- `GOOGLE_CALENDAR_WRITE_ACCOUNT=Pribadi` makes the write target explicit.
- The personal token must be re-authorized with Calendar write scope; the work token remains read-only.
- No extracted agenda item is persisted before the user confirms it.
- A valid timed item goes to personal Google Calendar; untimed items use SQLite.
- Calendar write failures use SQLite fallback and report the remote failure honestly.
- `/agenda` reads the same SQLite database used by `/agenda tambah` and verifies local writes before reporting success.

---

### Task 1: OAuth Scope and Google Calendar Write

**Files:**
- Modify: `config/settings.py` with `google_calendar_write_account` and login scope setting
- Modify: `connectors/gmail.py` scopes/constants used for token loading
- Modify: `connectors/gcal.py` write account selection and event insertion
- Modify: `scripts/google_login.py` write-scope mode
- Modify: `.env.example` OAuth settings
- Test: `tests/test_gcal_write.py`

**Interfaces:**
- `GoogleCalendarConnector.create_event(title, start, end, location, notes) -> dict`.
- `GoogleCalendarConnector.write_account_label -> str`.
- `scripts.google_login` accepts `GOOGLE_CALENDAR_WRITE=1` for the personal token.

- [ ] **Step 1: Write failing tests for account selection and event payload**

```python
from connectors.gcal import GoogleCalendarConnector


def test_write_account_is_configured_label(monkeypatch):
    monkeypatch.setattr("connectors.gcal.settings.google_calendar_write_account", "Pribadi")
    connector = GoogleCalendarConnector()

    assert connector.write_account_label == "Pribadi"


def test_event_payload_uses_personal_calendar(monkeypatch):
    connector = GoogleCalendarConnector()
    captured = {}

    class Events:
        def insert(self, **kwargs):
            captured.update(kwargs)
            return type("Request", (), {"execute": lambda self: {"id": "event-1", "htmlLink": "https://calendar"}})()

    class Service:
        def events(self):
            return Events()

    monkeypatch.setattr(connector, "_write_service", lambda: Service())

    result = connector.create_event("Rapat", "2026-07-20T10:00:00+07:00", "2026-07-20T11:00:00+07:00", "Jakarta", "Catatan")

    assert result["id"] == "event-1"
    assert captured["calendarId"] == "primary"
    assert captured["body"]["summary"] == "Rapat"
```

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `PYTHONPATH=. pytest tests/test_gcal_write.py -q`

Expected: FAIL because the write-account property and `create_event` are absent.

- [ ] **Step 3: Add explicit write-account settings**

Add:

```python
google_calendar_write_account: str = field(
    default_factory=lambda: _get("GOOGLE_CALENDAR_WRITE_ACCOUNT", "Pribadi")
)
google_calendar_write: bool = field(
    default_factory=lambda: _get("GOOGLE_CALENDAR_WRITE", "0") == "1"
)
```

Keep the normal read scopes unchanged. Add `calendar.events` only when the login script is run with `GOOGLE_CALENDAR_WRITE=1`; this prevents the work account from requesting write permission.

- [ ] **Step 4: Implement write account selection and `events.insert`**

Select the loaded account whose label equals `settings.google_calendar_write_account`; never select another account as a silent fallback. Build:

```python
{
    "summary": title,
    "location": location or "",
    "description": notes or "",
    "start": {"dateTime": start, "timeZone": settings.tz},
    "end": {"dateTime": end, "timeZone": settings.tz},
}
```

Raise actionable errors for missing account, invalid scope, disabled API, and Google HTTP errors.

- [ ] **Step 5: Document personal token re-login**

Add `.env.example`:

```env
GOOGLE_CALENDAR_WRITE_ACCOUNT=Pribadi
GOOGLE_CALENDAR_WRITE=0
```

Document the command:

```bash
rm "/path/ke/credentials/google_token.json"
GOOGLE_CALENDAR_WRITE=1 \
GOOGLE_CLIENT_SECRETS="/path/ke/credentials/client_secret_pribadi.json" \
GOOGLE_TOKEN_PATH="/path/ke/credentials/google_token.json" \
python -m scripts.google_login
```

- [ ] **Step 6: Run focused tests**

Run: `PYTHONPATH=. pytest tests/test_gcal_write.py -q`

Expected: PASS.

### Task 2: Gemini Image/PDF Agenda Extraction

**Files:**
- Modify: `core/llm_client.py`
- Create: `core/agenda_extraction.py`
- Test: `tests/test_agenda_extraction.py`

**Interfaces:**
- `normalize_agenda_items(data: dict | list) -> list[dict]`.
- `LLMClient.extract_agenda_attachment(content: bytes, mime_type: str) -> list[dict]`.

- [ ] **Step 1: Write failing normalization tests**

```python
from core.agenda_extraction import normalize_agenda_items


def test_normalize_agenda_items_keeps_all_items():
    items = normalize_agenda_items({"agendas": [{"title": "Rapat", "date": "2026-07-20"}, {"title": "Makan"}]})

    assert len(items) == 2
    assert items[0]["title"] == "Rapat"
    assert items[1]["needs_review"] is True


def test_normalize_rejects_empty_titles():
    assert normalize_agenda_items({"agendas": [{"title": ""}]}) == []
```

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `PYTHONPATH=. pytest tests/test_agenda_extraction.py -q`

Expected: collection failure because `core.agenda_extraction` does not exist.

- [ ] **Step 3: Implement normalized agenda schema**

Normalize each item to `title`, `date`, `start_time`, `end_time`, `location`, `notes`, and `needs_review`. Mark missing title/date/time, invalid dates, or ambiguous times for review. Do not drop valid sibling items because one item is malformed.

- [ ] **Step 4: Add Gemini multimodal extraction**

Send the attachment bytes with its MIME type and an instruction requiring JSON only:

```json
{"agendas":[{"title":"...","date":"YYYY-MM-DD","start_time":"HH:MM","end_time":"HH:MM","location":"...","notes":"...","needs_review":false}]}
```

Support `image/jpeg`, `image/png`, `image/webp`, and `application/pdf`. Return an actionable unavailable/error result when Gemini is disabled or extraction fails.

- [ ] **Step 5: Run extraction tests**

Run: `PYTHONPATH=. pytest tests/test_agenda_extraction.py -q`

Expected: PASS.

### Task 3: Agenda Save and `/agenda` Consistency

**Files:**
- Modify: `connectors/agenda_manual.py`
- Modify: `core/agent.py`
- Modify: `core/telegram_bot.py`
- Test: `tests/test_agenda_save.py`

**Interfaces:**
- `HermesAgent.save_agenda_item(item: dict) -> str`.
- `ManualAgendaConnector.add_verified(title, when_at, notes) -> dict`.

- [ ] **Step 1: Write failing read-after-write test**

```python
from core.db import Database
from connectors.agenda_manual import ManualAgendaConnector


def test_add_verified_reads_back_saved_row(tmp_path):
    agenda = ManualAgendaConnector(Database(str(tmp_path / "state.db")))

    saved = agenda.add_verified("Rapat", "2026-07-20T10:00:00+07:00", "Catatan")

    assert saved["title"] == "Rapat"
    assert saved["when_at"] == "2026-07-20T10:00:00+07:00"
```

- [ ] **Step 2: Run focused test and confirm it fails**

Run: `PYTHONPATH=. pytest tests/test_agenda_save.py -q`

Expected: FAIL because `add_verified` is absent.

- [ ] **Step 3: Implement verified local save**

Insert the row, query it by returned ID, and raise if it cannot be read back. `HermesAgent.save_agenda_item` uses Google Calendar only when date and start time are valid; otherwise it calls `add_verified` and reports SQLite storage.

- [ ] **Step 4: Implement Google-first fallback behavior**

For a timed item, call `gcal.create_event`. On success, return the event link and do not create a duplicate local row. On failure, call `add_verified` with a fallback note and return a message explaining that the item is local only.

- [ ] **Step 5: Run agenda save tests**

Run: `PYTHONPATH=. pytest tests/test_agenda_save.py -q`

Expected: PASS.

### Task 4: Telegram Attachments and One-by-One Confirmation

**Files:**
- Modify: `core/telegram_bot.py` handlers and pending state
- Modify: `core/agent.py` attachment extraction/save methods
- Test: `tests/test_telegram_agenda_attachment.py`

**Interfaces:**
- Register `MessageHandler(filters.PHOTO | filters.Document.ALL, self.on_attachment)`.
- `TelegramInterface.on_attachment(update, ctx)` downloads supported files, extracts all agenda items, and queues them.
- Callback data uses `agenda_confirm:<token>` and `agenda_skip:<token>`.

- [ ] **Step 1: Write failing handler test**

```python
def test_attachment_handler_exists():
    from core.telegram_bot import TelegramInterface

    assert hasattr(TelegramInterface, "on_attachment")
```

- [ ] **Step 2: Run focused test and confirm it fails**

Run: `PYTHONPATH=. pytest tests/test_telegram_agenda_attachment.py -q`

Expected: FAIL because `on_attachment` is absent.

- [ ] **Step 3: Implement attachment detection and download**

For a photo, use the highest-resolution `update.message.photo[-1]`. For a document, accept only PDF and supported image MIME types. Download to a `tempfile.NamedTemporaryFile`, read bytes, and delete it in `finally`. Reject unsupported files with a format explanation.

- [ ] **Step 4: Implement pending item queue and confirmation callbacks**

Store each extracted item in `self.pending_agenda`. Send one item at a time with title/date/time/location/notes and buttons. On **Catat**, call `agent.save_agenda_item`; on **Lewati**, discard only that item and show the next one. After the last item, report the count saved/skipped and destinations.

- [ ] **Step 5: Keep `/agenda tambah` consistent**

Route parsed date/time entries through `agent.save_agenda_item`, while title-only entries use verified local storage. Update `/agenda` responses to show source and actionable errors.

- [ ] **Step 6: Run attachment tests**

Run: `PYTHONPATH=. pytest tests/test_telegram_agenda_attachment.py -q`

Expected: PASS.

### Task 5: Documentation and Full Verification

**Files:**
- Modify: `README.md` agenda, OAuth, and Telegram sections
- Test: full suite

- [ ] **Step 1: Document account permissions and re-login**

Document that `Pribadi` is Calendar write-enabled, `Kerja` is Gmail-only, and show the personal token re-login command with `GOOGLE_CALENDAR_WRITE=1`.

- [ ] **Step 2: Document image/PDF usage**

Add examples for sending a photo/PDF, the one-by-one confirmation flow, supported MIME types, and fallback behavior.

- [ ] **Step 3: Run all tests**

Run: `PYTHONPATH=. pytest -q`

Expected: all tests pass with exit code 0.

- [ ] **Step 4: Rebuild and verify the service**

Run: `docker compose up --build -d`

Run: `docker compose ps`

Expected: `hermes` reports `healthy`.
