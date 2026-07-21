# Agenda Confirm → Local + ICS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route every agenda create/edit through Telegram Acc/Edit/Lewati, persist only to SQLite, send `.ics` for device Calendar, and remove Gmail/Google Calendar/Outlook APIs from Hermes.

**Architecture:** Extend the existing in-memory `pending_agenda` queue with Acc/Edit/Lewati and reply-based field edits. Acc writes an expanded local `agenda` row, builds a single-event `.ics`, and schedules Hermes Telegram reminders via a FastAPI lifespan loop. Remote calendar connectors and OAuth scripts are deleted; briefing reads local agenda only.

**Tech Stack:** Python 3.11, python-telegram-bot 21.9, Gemini, SQLite, pytest, stdlib `zoneinfo` for ICS timestamps (no new calendar SDK).

## Global Constraints

- No agenda row is written before the user taps **Acc**.
- Acc with `date` + `start_time` → SQLite + `.ics`; Acc without timed fields → SQLite only, no `.ics`.
- `.ics` `UID` is `hermes-agenda-{id}@hermes.local` (stable after first save).
- Edit before Acc uses button **✏️ Edit** then a free-form reply; invalid parse keeps draft unchanged.
- All inputs (image/PDF, NL `agenda_add`, `/agenda tambah`, `/agenda edit <id>`) use the same confirm card.
- Remove Gmail, Google Calendar OAuth, and Outlook from runtime/config/scripts/tests on the agenda path.
- Briefing and `/agenda` list local SQLite only.
- Timezone for ICS/`when_at` comes from `settings.tz` (default `Asia/Jakarta`).
- Do not commit unless the user explicitly asks during execution; plan commit steps are optional checkpoints.

## File Map

| File | Responsibility |
|---|---|
| `core/db.py` | Migrate `agenda` columns |
| `connectors/agenda_manual.py` | CRUD for expanded fields + due reminders |
| `core/agenda_ics.py` | Build `.ics` bytes from a saved row/draft |
| `core/agenda_extraction.py` | Normalize + `parse_agenda_edit_reply` + recommendation field |
| `core/agent.py` | Local-only `save_agenda_item` / `update_agenda_item`; drop GCal/Outlook |
| `core/telegram_bot.py` | Acc/Edit/Lewati UI, edit mode, enqueue all creates/edits, send `.ics` |
| `core/agenda_reminders.py` | Due-reminder query + message text |
| `core/main.py` | Lifespan loop to send Telegram reminders |
| `core/briefing.py` | Local-only agenda copy (source label Manual) |
| `config/settings.py`, `.env.example`, `README.md` | Drop Google/MS calendar env docs |
| Delete | `connectors/gcal.py`, `connectors/google_auth.py`, `connectors/outlook.py`, `scripts/google_login.py`, `scripts/ms_login.py` |
| Tests | New/updated under `tests/test_agenda_*.py`; remove/replace GCal/Outlook tests |

---

### Task 1: Expand agenda schema and connector CRUD

**Files:**
- Modify: `core/db.py`
- Modify: `connectors/agenda_manual.py`
- Test: `tests/test_agenda_save.py`
- Test: `tests/test_agenda_schema.py` (create)

**Interfaces:**
- Produces: `ManualAgendaConnector.add_item(item: dict) -> dict`
- Produces: `ManualAgendaConnector.update_item(agenda_id: int, item: dict) -> dict`
- Produces: `ManualAgendaConnector.get(agenda_id: int) -> dict | None`
- Produces: `ManualAgendaConnector.due_reminders(now_iso: str) -> list[dict]`
- Item keys: `title`, `date`, `start_time`, `end_time`, `location`, `notes`, `reminder_minutes`, `recommendation` (not stored), `reminders` (list → first minutes)

- [ ] **Step 1: Write failing tests for expanded save/get/update**

```python
from core.db import Database
from connectors.agenda_manual import ManualAgendaConnector


def test_add_item_stores_extended_fields(tmp_path):
    agenda = ManualAgendaConnector(Database(str(tmp_path / "state.db")))
    saved = agenda.add_item({
        "title": "Rapat",
        "date": "2026-07-20",
        "start_time": "10:00",
        "end_time": "11:00",
        "location": "Zoom",
        "notes": "bawa deck",
        "reminder_minutes": 30,
    })
    assert saved["id"] > 0
    assert saved["title"] == "Rapat"
    assert saved["date"] == "2026-07-20"
    assert saved["start_time"] == "10:00"
    assert saved["end_time"] == "11:00"
    assert saved["location"] == "Zoom"
    assert saved["reminder_minutes"] == 30
    assert saved["when_at"].startswith("2026-07-20T10:00")


def test_update_item_changes_fields(tmp_path):
    agenda = ManualAgendaConnector(Database(str(tmp_path / "state.db")))
    saved = agenda.add_item({"title": "A", "date": "2026-07-20", "start_time": "10:00"})
    updated = agenda.update_item(saved["id"], {"title": "B", "date": "2026-07-21", "start_time": "15:00", "reminder_minutes": 15})
    assert updated["title"] == "B"
    assert updated["date"] == "2026-07-21"
    assert updated["start_time"] == "15:00"
    assert updated["reminder_minutes"] == 15
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `PYTHONPATH=. pytest tests/test_agenda_save.py tests/test_agenda_schema.py -q`

Expected: FAIL (`add_item` missing and/or columns missing).

- [ ] **Step 3: Migrate schema in `Database._init_schema`**

Keep `CREATE TABLE IF NOT EXISTS agenda` compatible, then after create run additive migrations (ignore duplicate-column errors):

```python
ALTER TABLE agenda ADD COLUMN date TEXT;
ALTER TABLE agenda ADD COLUMN start_time TEXT;
ALTER TABLE agenda ADD COLUMN end_time TEXT;
ALTER TABLE agenda ADD COLUMN location TEXT;
ALTER TABLE agenda ADD COLUMN reminder_minutes INTEGER;
ALTER TABLE agenda ADD COLUMN reminder_sent_at TEXT;
ALTER TABLE agenda ADD COLUMN updated_at TEXT;
```

Derive `when_at` on write as `{date}T{start_time}:00` plus offset from `settings.tz` when both date and start_time exist; else keep `when_at` nullable.

- [ ] **Step 4: Implement connector methods**

- `add_item` / `update_item` insert/update all columns; on update clear `reminder_sent_at` when date/start_time/reminder_minutes change.
- `get(id)` returns full dict or `None`.
- `list_all` / `today_events` SELECT new columns; `today_events` filters on `date` or `substr(when_at,1,10)`.
- `due_reminders(now_iso)`: rows with `reminder_minutes IS NOT NULL`, timed `when_at`, `reminder_sent_at IS NULL`, and `when_at - reminder_minutes` <= now.
- Keep `add` / `add_verified` as thin wrappers calling `add_item` for old tests, or update callers.

- [ ] **Step 5: Run tests — expect PASS**

Run: `PYTHONPATH=. pytest tests/test_agenda_save.py tests/test_agenda_schema.py -q`

Expected: PASS

- [ ] **Step 6: Commit (only if user asked)**

```bash
git add core/db.py connectors/agenda_manual.py tests/test_agenda_save.py tests/test_agenda_schema.py
git commit -m "$(cat <<'EOF'
feat: expand local agenda schema for ICS and reminders

EOF
)"
```

---

### Task 2: ICS builder

**Files:**
- Create: `core/agenda_ics.py`
- Test: `tests/test_agenda_ics.py`

**Interfaces:**
- Consumes: saved agenda dict with `id`, `title`, `date`, `start_time`, `end_time`, `location`, `notes`, `reminder_minutes`
- Produces: `build_agenda_ics(item: dict, *, tz_name: str) -> bytes`
- Raises: `ValueError` if title empty or missing date/start_time

- [ ] **Step 1: Write failing ICS tests**

```python
from core.agenda_ics import build_agenda_ics


def test_build_agenda_ics_contains_uid_and_times():
    raw = build_agenda_ics(
        {
            "id": 7,
            "title": "Rapat Tim",
            "date": "2026-07-20",
            "start_time": "15:00",
            "end_time": "16:00",
            "location": "Zoom",
            "notes": "bawa deck",
            "reminder_minutes": 30,
        },
        tz_name="Asia/Jakarta",
    )
    text = raw.decode("utf-8")
    assert "BEGIN:VCALENDAR" in text
    assert "UID:hermes-agenda-7@hermes.local" in text
    assert "SUMMARY:Rapat Tim" in text
    assert "LOCATION:Zoom" in text
    assert "BEGIN:VALARM" in text
    assert "TRIGGER:-PT30M" in text


def test_build_agenda_ics_requires_start():
    try:
        build_agenda_ics({"id": 1, "title": "X", "date": "", "start_time": ""}, tz_name="Asia/Jakarta")
        assert False, "expected ValueError"
    except ValueError:
        pass
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `PYTHONPATH=. pytest tests/test_agenda_ics.py -q`

Expected: FAIL (module missing)

- [ ] **Step 3: Implement `core/agenda_ics.py`**

Minimal RFC5545 calendar:

- `VERSION:2.0`, `PRODID:-//Hermes//Agenda//ID`, `CALSCALE:GREGORIAN`, `METHOD:PUBLISH`
- `DTSTART`/`DTEND` as local floating times with `TZID={tz_name}` **or** UTC `Z` form — prefer floating + `TZID` using `ZoneInfo(tz_name)`
- Default end = start + 1 hour when `end_time` empty
- Escape `,;\\` and newlines in text fields
- Optional `VALARM` `ACTION:DISPLAY` when `reminder_minutes` set
- Return UTF-8 bytes ending with `\r\n` line endings

- [ ] **Step 4: Run test — expect PASS**

Run: `PYTHONPATH=. pytest tests/test_agenda_ics.py -q`

Expected: PASS

- [ ] **Step 5: Commit (only if user asked)**

```bash
git add core/agenda_ics.py tests/test_agenda_ics.py
git commit -m "$(cat <<'EOF'
feat: generate single-event ICS for local agenda

EOF
)"
```

---

### Task 3: Edit-reply parser and recommendation normalization

**Files:**
- Modify: `core/agenda_extraction.py`
- Test: `tests/test_agenda_extraction.py`

**Interfaces:**
- Produces: `parse_agenda_edit_reply(text: str, current: dict) -> dict` (merged draft; raises `ValueError` if nothing parsed)
- `normalize_agenda_items` also copies `recommendation` string when present

- [ ] **Step 1: Write failing parser tests**

```python
from core.agenda_extraction import parse_agenda_edit_reply, normalize_agenda_items


def test_parse_agenda_edit_reply_updates_fields():
    current = {
        "title": "A",
        "date": "2026-07-20",
        "start_time": "10:00",
        "end_time": "",
        "location": "",
        "notes": "",
        "reminders": [],
        "needs_review": False,
        "recommendation": "dari undangan",
    }
    updated = parse_agenda_edit_reply(
        "judul: Rapat Tim\njam: 15:00-16:00\nlokasi: Zoom\nreminder: 30m",
        current,
    )
    assert updated["title"] == "Rapat Tim"
    assert updated["start_time"] == "15:00"
    assert updated["end_time"] == "16:00"
    assert updated["location"] == "Zoom"
    assert updated["reminders"][0]["minutes"] == 30


def test_parse_agenda_edit_reply_rejects_empty():
    try:
        parse_agenda_edit_reply("halo saja", {"title": "A"})
        assert False
    except ValueError:
        pass


def test_normalize_keeps_recommendation():
    items = normalize_agenda_items({"agendas": [{"title": "X", "recommendation": "cek jam"}]})
    assert items[0]["recommendation"] == "cek jam"
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `PYTHONPATH=. pytest tests/test_agenda_extraction.py::test_parse_agenda_edit_reply_updates_fields -q`

Expected: FAIL

- [ ] **Step 3: Implement parser**

Support keys (case-insensitive, Indonesian): `judul`/`title`, `tanggal`/`date`, `jam`/`waktu`/`time` (`HH:MM` or `HH:MM-HH:MM`), `lokasi`/`location`, `catatan`/`notes`, `reminder` (`30m` / `30 menit`). Line-based `key: value`. Merge onto a copy of `current`. Map reminder into `reminders: [{"method": "popup", "minutes": N}]`.

Also set `recommendation` in `normalize_agenda_items` from raw.

- [ ] **Step 4: Run tests — expect PASS**

Run: `PYTHONPATH=. pytest tests/test_agenda_extraction.py -q`

Expected: PASS

- [ ] **Step 5: Commit (only if user asked)**

```bash
git add core/agenda_extraction.py tests/test_agenda_extraction.py
git commit -m "$(cat <<'EOF'
feat: parse agenda edit replies before Acc

EOF
)"
```

---

### Task 4: Local-only save/update service returning ICS payload

**Files:**
- Create: `core/agenda_service.py`
- Modify: `core/agent.py` (thin wrappers; remove GCal write from `save_agenda_item`)
- Test: `tests/test_agenda_accept.py`

**Interfaces:**
- Produces: `accept_agenda_item(agenda: ManualAgendaConnector, item: dict, *, agenda_id: int | None = None, tz_name: str) -> dict`
  - Returns `{"saved": dict, "ics": bytes | None, "message": str}`
- `reminder_minutes` derived from `item["reminder_minutes"]` or first of `item["reminders"]`
- No Google/Outlook calls

- [ ] **Step 1: Write failing accept tests**

```python
from core.db import Database
from connectors.agenda_manual import ManualAgendaConnector
from core.agenda_service import accept_agenda_item


def test_accept_agenda_item_local_and_ics(tmp_path):
    agenda = ManualAgendaConnector(Database(str(tmp_path / "state.db")))
    result = accept_agenda_item(
        agenda,
        {
            "title": "Rapat",
            "date": "2026-07-20",
            "start_time": "10:00",
            "end_time": "11:00",
            "location": "",
            "notes": "",
            "reminders": [{"method": "popup", "minutes": 30}],
        },
        tz_name="Asia/Jakarta",
    )
    assert result["saved"]["id"] > 0
    assert result["ics"] is not None
    assert b"BEGIN:VCALENDAR" in result["ics"]
    assert "✅" in result["message"]


def test_accept_without_time_skips_ics(tmp_path):
    agenda = ManualAgendaConnector(Database(str(tmp_path / "x.db")))
    result = accept_agenda_item(
        agenda,
        {"title": "Ide", "date": "", "start_time": ""},
        tz_name="Asia/Jakarta",
    )
    assert result["saved"]["id"] > 0
    assert result["ics"] is None
    assert "lokal" in result["message"].casefold() or "tanpa" in result["message"].casefold() or "✅" in result["message"]
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `PYTHONPATH=. pytest tests/test_agenda_accept.py -q`

Expected: FAIL (module missing)

- [ ] **Step 3: Implement `core/agenda_service.py`**

```python
def accept_agenda_item(agenda, item, *, agenda_id=None, tz_name="Asia/Jakarta") -> dict:
    title = (item.get("title") or "").strip()
    if not title:
        raise ValueError("Judul agenda tidak boleh kosong.")
    reminder_minutes = item.get("reminder_minutes")
    if reminder_minutes is None and item.get("reminders"):
        reminder_minutes = item["reminders"][0].get("minutes")
    payload = {**item, "title": title, "reminder_minutes": reminder_minutes}
    saved = agenda.update_item(agenda_id, payload) if agenda_id else agenda.add_item(payload)
    ics = None
    message = f"✅ Agenda [{saved['id']}] tersimpan di agenda lokal."
    if saved.get("date") and saved.get("start_time"):
        try:
            from core.agenda_ics import build_agenda_ics
            ics = build_agenda_ics(saved, tz_name=tz_name)
            message += " File .ics siap dibuka di Calendar HP/Mac."
        except ValueError:
            message += " (tanpa .ics — lengkapi tanggal/jam via Edit)."
        except Exception as exc:  # noqa: BLE001
            message += f" (local OK; .ics gagal: {exc})"
    else:
        message += " Belum ada .ics karena tanggal/jam kosong."
    return {"saved": saved, "ics": ics, "message": message}
```

Wire `HermesAgent.accept_agenda_item` as a one-liner calling this with `self.agenda` and `settings.tz`. Replace GCal logic in `save_agenda_item` by delegating to `accept_agenda_item` and returning only `message` until Telegram Task 5 consumes the full dict.

- [ ] **Step 4: Run test — expect PASS**

Run: `PYTHONPATH=. pytest tests/test_agenda_accept.py -q`

Expected: PASS

- [ ] **Step 5: Commit (only if user asked)**

```bash
git add core/agenda_service.py core/agent.py tests/test_agenda_accept.py
git commit -m "$(cat <<'EOF'
feat: accept agenda locally and attach ICS bytes

EOF
)"
```

---

### Task 5: Telegram Acc / Edit / Lewati + send ICS

**Files:**
- Modify: `core/telegram_bot.py`
- Test: `tests/test_telegram_agenda_confirm.py` (create)
- Update: `tests/test_telegram_agenda_attachment.py` if it asserts Catat

**Interfaces:**
- Consumes: `accept_agenda_item`, `parse_agenda_edit_reply`
- Callback prefixes: `agenda_confirm:`, `agenda_skip:`, `agenda_edit:`
- State keys on `pending_agenda[token]`: `items`, `total`, `saved`, `skipped`, `message`, `agenda_id` (optional, for edit-saved), `awaiting_edit` (bool), `card_message_id` (optional)

- [ ] **Step 1: Write failing unit tests for card keyboard and edit apply**

```python
from core.telegram_bot import TelegramInterface


def test_agenda_keyboard_has_acc_edit_lewati():
    bot = TelegramInterface(agent=None, briefing=None)
    markup = bot._agenda_keyboard("abc123")
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert labels == ["✅ Acc", "✏️ Edit", "⏭️ Lewati"]


def test_apply_edit_reply_updates_head_item():
    bot = TelegramInterface(agent=None, briefing=None)
    bot.pending_agenda["tok"] = {
        "items": [{"title": "A", "date": "2026-07-20", "start_time": "10:00", "end_time": "", "location": "", "notes": "", "reminders": [], "needs_review": False}],
        "total": 1,
        "saved": 0,
        "skipped": 0,
        "awaiting_edit": True,
        "message": None,
    }
    updated = bot._apply_agenda_edit_reply("tok", "judul: Baru")
    assert updated["title"] == "Baru"
    assert bot.pending_agenda["tok"]["awaiting_edit"] is False
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `PYTHONPATH=. pytest tests/test_telegram_agenda_confirm.py -q`

Expected: FAIL

- [ ] **Step 3: Implement UI helpers and callbacks**

1. `_agenda_keyboard(token)` → Acc / Edit / Lewati  
2. `_format_agenda_card(item, index, total)` include `Rekomendasi: …` when present  
3. `_send_next_agenda` uses Acc label and Edit button  
4. `_on_agenda_callback`:
   - `agenda_edit:` → set `awaiting_edit=True`, edit message to ask for correction format, do **not** pop item
   - `agenda_confirm:` → `accept_agenda_item` (pass `state.get("agenda_id")`), edit message with result text, if `ics` then `reply_document` with filename `agenda-{id}.ics` and caption instructing Add on phone/Mac Google calendar, then pop + next
   - `agenda_skip:` → if `agenda_id` set (edit session) treat as cancel without delete; else discard draft; pop + next
5. In `on_message`, **before** normal routing: if any `pending_agenda` for this chat has `awaiting_edit`, apply reply, re-send card (or edit card), return

Track `chat_id` on pending state when enqueueing so edit replies bind to the right token.

- [ ] **Step 4: Run tests — expect PASS**

Run: `PYTHONPATH=. pytest tests/test_telegram_agenda_confirm.py tests/test_telegram_agenda_attachment.py -q`

Expected: PASS

- [ ] **Step 5: Commit (only if user asked)**

```bash
git add core/telegram_bot.py tests/test_telegram_agenda_confirm.py tests/test_telegram_agenda_attachment.py
git commit -m "$(cat <<'EOF'
feat: Acc/Edit/Lewati agenda cards with ICS delivery

EOF
)"
```

---

### Task 6: Enqueue all create/edit paths (no immediate save)

**Files:**
- Modify: `core/telegram_bot.py` (`cmd_agenda`, `on_message` / plan handling)
- Modify: `core/agent.py` (`plan_message`, `handle_message` for `agenda_add` / `agenda_edit`)
- Test: `tests/test_agenda_enqueue.py` (create)

**Interfaces:**
- Produces: `TelegramInterface.enqueue_agenda_items(message, items: list[dict], *, agenda_id: int | None = None) -> None`
- Planner: `agenda_edit` with `{"id": number}`; remove reliance on immediate `save_agenda_item` for create
- NL “ubah agenda N” → `agenda_edit`

- [ ] **Step 1: Write failing tests for enqueue helper**

```python
import asyncio
from core.telegram_bot import TelegramInterface


def test_enqueue_agenda_items_sets_pending(monkeypatch):
    bot = TelegramInterface(agent=None, briefing=None)
    sent = []

    class Msg:
        async def reply_text(self, text, **kwargs):
            sent.append(text)
            return None

    async def fake_send(token):
        sent.append(f"card:{token}")

    monkeypatch.setattr(bot, "_send_next_agenda", fake_send)
    message = Msg()
    asyncio.get_event_loop().run_until_complete(
        bot.enqueue_agenda_items(message, [{"title": "X", "date": "2026-07-20", "start_time": "09:00", "end_time": "", "location": "", "notes": "", "reminders": [], "needs_review": False, "recommendation": "ok"}])
    )
    assert len(bot.pending_agenda) == 1
    assert any("1 agenda" in t or "Ditemukan" in t or "Draft" in t for t in sent)
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `PYTHONPATH=. pytest tests/test_agenda_enqueue.py -q`

Expected: FAIL

- [ ] **Step 3: Wire all entry points**

1. Attachment path calls `enqueue_agenda_items` (refactor existing pending setup)
2. `/agenda tambah …` → `parse_agenda_text` → enqueue (not `save_agenda_item`)
3. `/agenda edit <id>` → `agenda.get(id)` → map row to draft fields → enqueue with `agenda_id=id`
4. `handle_message` / bot layer: when plan `agenda_add`, enqueue instead of save; return a short “draft siap, Acc/Edit/Lewati” or let enqueue send cards and return `""` / skip duplicate reply
5. Plan heuristic: `ubah agenda <id>` → `agenda_edit`
6. Update `/agenda` help text: `| /agenda edit <id>`
7. Local list query (“cek agenda hari ini”) should read SQLite via `agenda.today_events` / `list_all` — replace `calendar_query` Google path with local list action `agenda_query` (simple string format). Remove `calendar_update_reminders` Google flow; optional later: edit reminder via `/agenda edit`

- [ ] **Step 4: Run related tests**

Run: `PYTHONPATH=. pytest tests/test_agenda_enqueue.py tests/test_agenda_delete.py tests/test_agenda_extraction.py -q`

Expected: PASS

- [ ] **Step 5: Commit (only if user asked)**

```bash
git add core/telegram_bot.py core/agent.py tests/test_agenda_enqueue.py
git commit -m "$(cat <<'EOF'
feat: require Acc for all agenda create and edit paths

EOF
)"
```

---

### Task 7: Hermes local Telegram reminders

**Files:**
- Create: `core/agenda_reminders.py`
- Modify: `core/main.py` lifespan
- Modify: `connectors/agenda_manual.py` (`mark_reminder_sent`)
- Test: `tests/test_agenda_reminders.py`

**Interfaces:**
- Produces: `due_agenda_reminder_messages(agenda: ManualAgendaConnector, now: datetime) -> list[tuple[dict, str]]`
- Produces: `mark_reminder_sent(agenda_id: int) -> None`
- Lifespan: every 60s, if Telegram ready, send due reminders to `settings.telegram_chat_id`

- [ ] **Step 1: Write failing due-reminder test**

```python
import datetime as dt
from zoneinfo import ZoneInfo
from core.db import Database
from connectors.agenda_manual import ManualAgendaConnector
from core.agenda_reminders import due_agenda_reminder_messages


def test_due_reminder_is_listed(tmp_path):
    agenda = ManualAgendaConnector(Database(str(tmp_path / "r.db")))
    tz = ZoneInfo("Asia/Jakarta")
    start = dt.datetime(2026, 7, 20, 10, 0, tzinfo=tz)
    agenda.add_item({
        "title": "Rapat",
        "date": "2026-07-20",
        "start_time": "10:00",
        "reminder_minutes": 30,
    })
    now = start - dt.timedelta(minutes=30)
    due = due_agenda_reminder_messages(agenda, now)
    assert len(due) == 1
    assert "Rapat" in due[0][1]
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `PYTHONPATH=. pytest tests/test_agenda_reminders.py -q`

Expected: FAIL

- [ ] **Step 3: Implement reminder helper + loop**

- Query due rows; format: `🔔 Reminder: {title} pada {date} {start_time}`
- `mark_reminder_sent` sets `reminder_sent_at`
- In `core/main.py` lifespan, start `asyncio.create_task` loop: sleep 60, call helper, `bot.send_message` to configured chat; cancel on shutdown
- Skip loop cleanly if Telegram disabled

- [ ] **Step 4: Run test — expect PASS**

Run: `PYTHONPATH=. pytest tests/test_agenda_reminders.py -q`

Expected: PASS

- [ ] **Step 5: Commit (only if user asked)**

```bash
git add core/agenda_reminders.py core/main.py connectors/agenda_manual.py tests/test_agenda_reminders.py
git commit -m "$(cat <<'EOF'
feat: send local Telegram reminders for accepted agenda

EOF
)"
```

---

### Task 8: Remove Google Calendar, Gmail remnants, and Outlook

**Files:**
- Delete: `connectors/gcal.py`, `connectors/google_auth.py`, `connectors/outlook.py`, `scripts/google_login.py`, `scripts/ms_login.py`
- Modify: `core/agent.py`, `config/settings.py`, `.env.example`, `requirements.txt` (drop `msal`, Google OAuth libs if unused elsewhere — **keep** `google-generativeai` / Gemini quota service-account usage in `core/quota.py`)
- Modify/delete tests: `tests/test_gcal_write.py`, `tests/test_google_connectors.py`, `tests/test_calendar_query.py`, `tests/test_agenda_reminder_flow.py`, `tests/test_outlook_settings.py`
- Modify: `README.md`, `deploy/cloud.env.example`
- Keep Gemini quota service account env (`GEMINI_QUOTA_*`) — not Calendar OAuth

**Interfaces:**
- `HermesAgent.connectors` = `[agenda, tavily?]` — Tavily may not implement Connector; keep list as local agenda (+ any remaining Connector impls)
- No `self.gcal` / `self.outlook`

- [ ] **Step 1: Write a guard test that imports fail for removed modules (optional) OR update agent smoke test**

```python
def test_agent_has_no_gcal_outlook(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DB_PATH", str(tmp_path / "a.db"))
    # Prefer importing connectors list from a light factory if full HermesAgent is heavy
    from connectors.agenda_manual import ManualAgendaConnector
    assert ManualAgendaConnector.name == "Agenda Manual"
```

Better concrete check after refactor:

```python
import importlib
import pytest

@pytest.mark.parametrize("mod", ["connectors.gcal", "connectors.outlook", "connectors.google_auth"])
def test_remote_calendar_modules_removed(mod):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(mod)
```

- [ ] **Step 2: Run — expect FAIL (modules still exist)**

Run: `PYTHONPATH=. pytest tests/test_remote_calendar_removed.py -q`

Expected: FAIL (import succeeds)

- [ ] **Step 3: Remove modules and strip references**

- Delete connector/script files listed above
- Strip settings: `GOOGLE_*` calendar/token fields, `MS_*` (leave Gemini quota fields)
- Strip agent planning actions `calendar_query` / `calendar_update_reminders`
- Remove calendar reminder callback handlers in telegram bot
- Update `all_today_agenda` to only use `self.agenda.today_events()`
- Update requirements: remove `msal`, `google-api-python-client`, `google-auth-oauthlib` **only if** unused by quota/Gemini; inspect `core/quota.py` — if it needs `google-auth` + `google-api-python-client`, keep those two; remove `msal` and `google-auth-oauthlib`
- Fix/remove obsolete tests
- Update README setup (no Google/MS login); document `.ics` Acc flow

- [ ] **Step 4: Run full unit suite**

Run: `PYTHONPATH=. pytest -q`

Expected: PASS (skip or fix any finance tests that imported gmail helpers)

- [ ] **Step 5: Commit (only if user asked)**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: remove Google Calendar and Outlook integrations

EOF
)"
```

---

### Task 9: Briefing copy + docs polish

**Files:**
- Modify: `core/briefing.py`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-17-agenda-ics-local-confirm-design.md` status → `Implemented` only after code lands (optional)
- Test: `tests/test_briefing_nyx.py` (ensure still passes without GCal)

- [ ] **Step 1: Adjust briefing expectations if tests assert remote sources**

Update any assertion that expects Google/Outlook lines.

- [ ] **Step 2: Run briefing tests**

Run: `PYTHONPATH=. pytest tests/test_briefing_nyx.py -q`

Expected: PASS

- [ ] **Step 3: README section**

Document:

1. Send agenda text/image → Acc/Edit/Lewati  
2. Acc → local + `.ics`  
3. Open `.ics` on phone/Mac → Add to Google calendar account  
4. `/agenda edit <id>` to revise and re-export `.ics`  
5. No Google/Outlook OAuth for Hermes

- [ ] **Step 4: Final suite**

Run: `PYTHONPATH=. pytest -q`

Expected: PASS

- [ ] **Step 5: Commit (only if user asked)**

```bash
git add core/briefing.py README.md tests/test_briefing_nyx.py
git commit -m "$(cat <<'EOF'
docs: document local agenda Acc and ICS calendar flow

EOF
)"
```

---

## Spec Coverage Checklist

| Spec requirement | Task |
|---|---|
| Acc → SQLite + `.ics` | 4, 5 |
| Edit before Acc via button + reply | 3, 5 |
| All inputs through confirm | 6 |
| Edit saved agenda | 6 |
| Local briefing | 8, 9 |
| Local Telegram reminders | 7 |
| Remove Gmail/GCal/Outlook | 8 |
| Acc without time → local only | 4 |
| ICS UID stable per id | 2, 4 |
| Restart drops pending drafts | 5 (in-memory; no change) |

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-07-17-agenda-ics-local-confirm.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — execute tasks in this session with checkpoints  

Which approach?
