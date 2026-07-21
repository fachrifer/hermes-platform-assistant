# Gmail Finance Import Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore multi-account Gmail finance scanning (Pribadi + Kerja) behind dual-mode `/import`, keep paste/upload, and leave personal Calendar read/write unchanged.

**Architecture:** Reintroduce `GmailConnector` on shared Google OAuth (add `gmail.readonly`), extend `/import` parsing with `gmail` / `paste` / mode-picker, restore `FinanceImportService.import_period` to scan → `stage_from_sources`, and wire Gmail into the agent connector list for `/status`.

**Tech Stack:** Python 3.11, pytest, google-api-python-client, google-auth, python-telegram-bot, existing SQLite staging.

## Global Constraints

- Gmail is finance-scan only — never add inbox contents to `/brief`.
- Always combine Pribadi + Kerja Gmail; no account-picker command.
- Email IDs must be `{label}:{gmail_message_id}`.
- One failed Google account must not disable the other.
- Calendar read/write remains Pribadi-only (`GOOGLE_CALENDAR_WRITE_ACCOUNT`).
- Paste/upload session behavior must keep working.
- Never log OAuth token contents.
- Spec: `docs/superpowers/specs/2026-07-21-gmail-finance-import-restore-design.md`.

## File map

| File | Role |
|---|---|
| `connectors/google_auth.py` | Add `gmail.readonly` to `SCOPES` |
| `scripts/google_login.py` | Mention Gmail + Calendar APIs in errors/help |
| `connectors/gmail.py` | New multi-account Gmail connector |
| `core/finance_import.py` | Dual-mode parse + restore `import_period` |
| `core/agent.py` | Construct/register Gmail; pass into finance import |
| `core/telegram_bot.py` | Mode picker + gmail/paste routing |
| `core/briefing.py` | Drop stale “No Gmail” comment only |
| `.env.example`, `README.md` | Document dual import + re-login |
| `tests/test_gmail_connector.py` | Query, merge, health, IDs |
| `tests/test_finance_import.py` | Parse modes + Gmail import staging |
| `tests/test_import_mode_callback.py` | Callback data parsing / routing helpers |

---

### Task 1: Restore Gmail OAuth scope

**Files:**
- Modify: `connectors/google_auth.py`
- Modify: `scripts/google_login.py`
- Test: `tests/test_google_auth_scopes.py`

**Interfaces:**
- Produces `SCOPES` containing both:
  - `https://www.googleapis.com/auth/gmail.readonly`
  - `https://www.googleapis.com/auth/calendar.readonly`
- `scripts.google_login.login_scopes()` still appends `calendar.events` when `settings.google_calendar_write` is true.

- [ ] **Step 1: Write the failing test**

```python
from connectors.google_auth import SCOPES
from scripts.google_login import login_scopes


def test_scopes_include_gmail_and_calendar_readonly():
    assert "https://www.googleapis.com/auth/gmail.readonly" in SCOPES
    assert "https://www.googleapis.com/auth/calendar.readonly" in SCOPES


def test_login_scopes_add_calendar_events_when_write_enabled(monkeypatch):
    monkeypatch.setattr("scripts.google_login.settings.google_calendar_write", True)
    scopes = login_scopes()
    assert "https://www.googleapis.com/auth/calendar.events" in scopes
    assert "https://www.googleapis.com/auth/gmail.readonly" in scopes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_google_auth_scopes.py -q`

Expected: FAIL because `gmail.readonly` is missing from `SCOPES`.

- [ ] **Step 3: Update scopes and login copy**

In `connectors/google_auth.py`, set:

```python
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]
```

Update the module docstring to say Gmail + Calendar. In `scripts/google_login.py`, change the missing-secrets message to say enable **Gmail API** and **Google Calendar API**.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_google_auth_scopes.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add connectors/google_auth.py scripts/google_login.py tests/test_google_auth_scopes.py
git commit -m "feat: add gmail.readonly to Google OAuth scopes"
```

---

### Task 2: Implement `GmailConnector`

**Files:**
- Create: `connectors/gmail.py`
- Test: `tests/test_gmail_connector.py`

**Interfaces:**
- Consumes: `load_all_credentials()`, `GoogleAccount`, `settings.finance_senders_list`, `settings.finance_keywords_list`
- Produces:
  - `class GmailConnector(Connector)` with `name = "Gmail"`, `icon = "📧"`
  - `is_available() -> bool`
  - `async health_check() -> bool`
  - `_build_finance_range_query(start: date, end: date) -> str`
  - `scan_finance_range(start: date, end: date, max_results: int = 100) -> list[dict]`
  - Each dict: `id`, `account`, `from`, `subject`, `date`, `body`

- [ ] **Step 1: Write failing tests**

```python
from datetime import date

from connectors.gmail import GmailConnector


def test_finance_range_query_is_inclusive(monkeypatch):
    monkeypatch.setattr(
        "connectors.gmail.settings.finance_email_senders",
        "bank.example",
        raising=False,
    )
    # Prefer patching list properties if settings exposes them as properties:
    monkeypatch.setattr(
        "connectors.gmail.settings.finance_senders_list",
        ["bank.example"],
        raising=False,
    )
    monkeypatch.setattr(
        "connectors.gmail.settings.finance_keywords_list",
        ["transfer"],
        raising=False,
    )
    connector = GmailConnector()
    query = connector._build_finance_range_query(date(2026, 1, 1), date(2026, 1, 31))
    assert "after:2025/12/31" in query
    assert "before:2026/02/01" in query
    assert "from:(bank.example)" in query
    assert '"transfer"' in query


def test_scan_merges_accounts_and_qualifies_ids(monkeypatch):
    class FakeAccount:
        def __init__(self, label):
            self.label = label
            self.credentials = object()

    def fake_load():
        return [FakeAccount("Pribadi"), FakeAccount("Kerja")]

    monkeypatch.setattr("connectors.gmail.load_all_credentials", fake_load)

    connector = GmailConnector()

    def fake_scan_account(account, start, end, max_results):
        return [{
            "id": f"{account.label}:m1",
            "account": account.label,
            "from": "a@x",
            "subject": "s",
            "date": "2026-01-02",
            "body": "Transfer 10000",
        }]

    monkeypatch.setattr(connector, "_scan_account_range", fake_scan_account)
    rows = connector.scan_finance_range(date(2026, 1, 1), date(2026, 1, 31))
    assert {r["account"] for r in rows} == {"Pribadi", "Kerja"}
    assert all(":" in r["id"] for r in rows)


def test_scan_skips_failed_account(monkeypatch):
    class FakeAccount:
        def __init__(self, label):
            self.label = label
            self.credentials = object()

    monkeypatch.setattr(
        "connectors.gmail.load_all_credentials",
        lambda: [FakeAccount("Pribadi"), FakeAccount("Kerja")],
    )
    connector = GmailConnector()

    def fake_scan_account(account, start, end, max_results):
        if account.label == "Pribadi":
            raise RuntimeError("boom")
        return [{
            "id": "Kerja:m2",
            "account": "Kerja",
            "from": "b@x",
            "subject": "s",
            "date": "2026-01-03",
            "body": "ok",
        }]

    monkeypatch.setattr(connector, "_scan_account_range", fake_scan_account)
    rows = connector.scan_finance_range(date(2026, 1, 1), date(2026, 1, 31))
    assert len(rows) == 1
    assert rows[0]["account"] == "Kerja"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_gmail_connector.py -q`

Expected: FAIL with `ModuleNotFoundError` or missing attributes.

- [ ] **Step 3: Implement `connectors/gmail.py`**

Minimal structure:

```python
"""Multi-account Gmail connector for finance scan (readonly)."""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import logging
from email.utils import parsedate_to_datetime

from googleapiclient.discovery import build

from config.settings import settings
from connectors.google_auth import load_all_credentials
from core.connector import Connector

logger = logging.getLogger("hermes.gmail")


class GmailConnector(Connector):
    name = "Gmail"
    icon = "📧"

    def is_available(self) -> bool:
        from pathlib import Path
        return any(Path(path).exists() for path in settings.google_token_paths)

    def _build_finance_range_query(self, start: dt.date, end: dt.date) -> str:
        after = (start - dt.timedelta(days=1)).strftime("%Y/%m/%d")
        before = (end + dt.timedelta(days=1)).strftime("%Y/%m/%d")
        parts = [f"after:{after}", f"before:{before}"]
        senders = settings.finance_senders_list
        if senders:
            parts.append("from:(" + " OR ".join(senders) + ")")
        keywords = settings.finance_keywords_list
        if keywords:
            parts.append("(" + " OR ".join(f'"{k}"' for k in keywords) + ")")
        return " ".join(parts)

    def _service(self, credentials):
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def _extract_body(self, payload: dict) -> str:
        # Walk MIME parts; decode base64url text/plain (fallback text/html stripped lightly)
        ...

    def _scan_account_range(self, account, start, end, max_results) -> list[dict]:
        # list+paginate messages, get full, build dict with id=f"{account.label}:{msg_id}"
        ...

    def scan_finance_range(self, start: dt.date, end: dt.date, max_results: int = 100) -> list[dict]:
        rows: list[dict] = []
        for account in load_all_credentials():
            try:
                rows.extend(self._scan_account_range(account, start, end, max_results))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Gmail scan akun %s gagal: %s", account.label, exc)
        return rows

    async def health_check(self) -> bool:
        if not self.is_available():
            return False
        return await asyncio.to_thread(self._check_sync)

    def _check_sync(self) -> bool:
        for account in load_all_credentials():
            try:
                self._service(account.credentials).users().getProfile(userId="me").execute()
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Gmail health akun %s gagal: %s", account.label, exc)
        return False
```

Implement `_extract_body` and `_scan_account_range` fully (no placeholders). Reuse patterns from `docs/superpowers/plans/2026-07-16-historical-finance-import.md` for pagination and date operators.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_gmail_connector.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add connectors/gmail.py tests/test_gmail_connector.py
git commit -m "feat: restore multi-account Gmail finance connector"
```

---

### Task 3: Dual-mode `/import` parsing

**Files:**
- Modify: `core/finance_import.py` (`parse_import_command`, `parse_import_args`)
- Test: `tests/test_finance_import.py`

**Interfaces:**
- `parse_import_command(args) -> dict` with keys:
  - `action`: `"choose_mode" | "start_gmail" | "start_paste" | "done" | "cancel"`
  - `period`: `str | None`
  - `force`: `bool`
- Bare `/import` / `/import YYYY-MM` / `/import YYYY-MM force` → `choose_mode`
- `/import gmail ...` → `start_gmail`
- `/import paste ...` → `start_paste`
- `parse_import_args` remains start-only compatibility for paste/gmail callers that still expect `(period, force)`; update tests that assumed bare `[]` → `action=start` to expect `choose_mode`.

- [ ] **Step 1: Write failing parse tests**

```python
from core.finance_import import parse_import_command


def test_parse_import_command_dual_mode():
    assert parse_import_command([])["action"] == "choose_mode"
    assert parse_import_command(["2026-01"])["action"] == "choose_mode"
    assert parse_import_command(["2026-01", "force"])["force"] is True

    gmail = parse_import_command(["gmail", "2026-01", "force"])
    assert gmail == {"action": "start_gmail", "period": "2026-01", "force": True}

    paste = parse_import_command(["paste", "2026-07"])
    assert paste == {"action": "start_paste", "period": "2026-07", "force": False}

    assert parse_import_command(["done"])["action"] == "done"
    assert parse_import_command(["cancel"])["action"] == "cancel"
```

Update `test_parse_import_command_supports_session_and_force` so `parse_import_command([])["action"] == "choose_mode"` (not `"start"`).

- [ ] **Step 2: Run focused tests to verify failure**

Run: `PYTHONPATH=. pytest tests/test_finance_import.py::test_parse_import_command_dual_mode tests/test_finance_import.py::test_parse_import_command_supports_session_and_force -q`

Expected: FAIL on action mismatch / unknown `gmail` token.

- [ ] **Step 3: Implement parser**

Rewrite `parse_import_command`:

1. empty → `choose_mode` + `default_import_period()`
2. `done` / `cancel` unchanged
3. if first token in `{gmail, paste}` → consume mode, then optional period + force
4. else treat first token as period → `choose_mode`
5. error message lists both modes

For `gmail`/`paste` without period, use `default_import_period()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_finance_import.py -q`

Expected: PASS (fix any other parse assertions broken by the action rename).

- [ ] **Step 5: Commit**

```bash
git add core/finance_import.py tests/test_finance_import.py
git commit -m "feat: parse dual-mode /import gmail and paste"
```

---

### Task 4: Restore Gmail `import_period`

**Files:**
- Modify: `core/finance_import.py` (`FinanceImportService.__init__`, `import_period`)
- Test: `tests/test_finance_import.py`

**Interfaces:**
- Consumes: `GmailConnector.scan_finance_range`, `stage_from_sources`, `parse_import_period`
- `FinanceImportService(db, llm, gmail=None, spreadsheet_service=None)`
- `async import_period(period: str, *, force: bool = False) -> dict` stages Gmail sources (no `manual_intake`)
- If `self.gmail` is `None`, raise `RuntimeError` with paste suggestion text

- [ ] **Step 1: Write failing integration-style test**

```python
import pytest
from datetime import date

from core.db import Database
from core.finance_import import FinanceImportService


@pytest.mark.asyncio
async def test_import_period_stages_gmail_sources(tmp_path):
    class LLM:
        async def extract_transaction(self, body, categories):
            return {
                "is_transaction": True,
                "amount": 10000,
                "type": "expense",
                "category": "Makanan",
                "subcategory": "Restoran",
                "merchant": "Warung",
                "description": "Makan",
                "date": "2026-01-15",
            }

    class FakeGmail:
        def scan_finance_range(self, start, end, max_results=100):
            assert start == date(2026, 1, 1)
            assert end == date(2026, 1, 31)
            return [{
                "id": "Pribadi:abc",
                "account": "Pribadi",
                "from": "bank@example",
                "subject": "Transfer",
                "date": "2026-01-15",
                "body": "Transfer 10000 ke Warung",
            }]

    db = Database(str(tmp_path / "state.db"))
    service = FinanceImportService(db, LLM(), gmail=FakeGmail())
    result = await service.import_period("2026-01")

    assert result["staged"] == 1
    assert result.get("manual_intake") is not True
    assert db.is_email_handled("Pribadi:abc")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_finance_import.py::test_import_period_stages_gmail_sources -q`

Expected: FAIL because `import_period` returns `manual_intake` / stages 0.

- [ ] **Step 3: Implement `import_period`**

```python
async def import_period(self, period: str, *, force: bool = False) -> dict:
    if self.gmail is None:
        raise RuntimeError(
            "Gmail belum terhubung. Jalankan google_login, atau pakai /import paste."
        )
    cleared = 0
    if force:
        cleared = self.db.clear_skipped_emails(summary="bukan transaksi")
    start_s, end_s = parse_import_period(period if len(period) == 7 else period)
    # For YYYY-MM period string, parse_import_period already returns month bounds.
    start = dt.date.fromisoformat(start_s)
    end = dt.date.fromisoformat(end_s)
    sources = await asyncio.to_thread(self.gmail.scan_finance_range, start, end)
    result = await self.stage_from_sources(sources, period_hint=period, force=False)
    result["cleared_skips"] = cleared
    result["period"] = period if len(period) == 7 else result.get("period")
    return result
```

Keep constructor `gmail=None` default; store as `self.gmail`.

- [ ] **Step 4: Run finance import tests**

Run: `PYTHONPATH=. pytest tests/test_finance_import.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/finance_import.py tests/test_finance_import.py
git commit -m "feat: restore Gmail-backed finance import_period"
```

---

### Task 5: Wire Gmail into the agent

**Files:**
- Modify: `core/agent.py`
- Modify: `core/briefing.py` (comment only)
- Test: `tests/test_agent_gmail_wiring.py`

**Interfaces:**
- Consumes: `GmailConnector`
- Produces: `self.gmail`, `self.finance_import` constructed with `gmail=self.gmail`, `self.connectors` includes Gmail before Calendar

- [ ] **Step 1: Write failing test**

```python
from core.agent import HermesAgent


def test_agent_registers_gmail_connector(monkeypatch):
    # Avoid real Google/network: patch connectors' is_available/health if constructor is heavy.
    agent = HermesAgent()
    assert any(c.name == "Gmail" for c in agent.connectors)
    assert agent.finance_import.gmail is agent.gmail
```

If `HermesAgent()` is too heavy for unit tests in this repo, instead assert via a small factory or monkeypatch settings paths; follow existing agent test patterns if present.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_agent_gmail_wiring.py -q`

Expected: FAIL (Gmail not registered).

- [ ] **Step 3: Wire agent**

```python
from connectors.gmail import GmailConnector

self.gmail = GmailConnector()
self.gcal = GoogleCalendarConnector()
...
self.finance_import = FinanceImportService(
    self.db, self.llm, gmail=self.gmail, spreadsheet_service=self.spreadsheet_finance
)
self.connectors = [self.gmail, self.gcal, self.agenda]
```

Update the “Gmail removed” comment. In `core/briefing.py`, change the module docstring to remove “No Gmail” (status may list Gmail).

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest tests/test_agent_gmail_wiring.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/agent.py core/briefing.py tests/test_agent_gmail_wiring.py
git commit -m "feat: register Gmail connector on Hermes agent"
```

---

### Task 6: Telegram mode picker and routing

**Files:**
- Modify: `core/telegram_bot.py` (`cmd_import`, `on_callback`)
- Create: `tests/test_import_mode_callback.py`

**Interfaces:**
- Produces helper `build_import_mode_keyboard(period: str, force: bool) -> InlineKeyboardMarkup`
- Callback data: `import_mode:gmail:{period}:{0|1}` and `import_mode:paste:{period}:0`
- `cmd_import`:
  - `choose_mode` → send picker
  - `start_gmail` → call `agent.finance_import.import_period` and summarize
  - `start_paste` → existing session open logic
  - `done` / `cancel` unchanged
- `on_callback`: handle `import_mode:` prefix before other batch callbacks

- [ ] **Step 1: Write failing helper tests**

```python
from core.telegram_bot import build_import_mode_keyboard, parse_import_mode_callback


def test_import_mode_keyboard_callback_data():
    markup = build_import_mode_keyboard("2026-07", True)
    data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert "import_mode:gmail:2026-07:1" in data
    assert "import_mode:paste:2026-07:0" in data


def test_parse_import_mode_callback():
    assert parse_import_mode_callback("import_mode:gmail:2026-07:1") == {
        "mode": "gmail",
        "period": "2026-07",
        "force": True,
    }
    assert parse_import_mode_callback("import_mode:paste:2026-01:0")["mode"] == "paste"
    assert parse_import_mode_callback("batch_ok:x") is None
```

If helpers must live as methods on the bot class for style consistency, export thin module-level wrappers used by tests.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_import_mode_callback.py -q`

Expected: FAIL (helpers missing).

- [ ] **Step 3: Implement helpers + `cmd_import` branches + callback**

`cmd_import` sketch:

```python
cmd = parse_import_command(list(ctx.args or []))
if cmd["action"] == "choose_mode":
    await update.message.reply_text(
        f"📥 Import {cmd['period']}\nPilih sumber:",
        reply_markup=build_import_mode_keyboard(cmd["period"], cmd["force"]),
    )
    return
if cmd["action"] == "start_gmail":
    await update.message.reply_text(f"📧 Memindai Gmail untuk {cmd['period']}…")
    try:
        result = await self.agent.finance_import.import_period(cmd["period"], force=cmd["force"])
    except Exception as exc:
        await update.message.reply_text(f"⚠️ {exc}\nCoba /import paste {cmd['period']}")
        return
    await update.message.reply_text(
        f"📥 Import Gmail {cmd['period']} selesai.\n"
        f"Masuk staging: {result.get('staged', 0)}\n"
        f"Dilewati: {result.get('skipped', 0)}\n"
        f"Gagal ekstrak: {result.get('extract_errors', 0)}\n\n"
        f"Review: /batch {cmd['period']}"
    )
    return
if cmd["action"] == "start_paste":
    # existing session open body (moved from previous start path)
    ...
```

In `on_callback`, when data starts with `import_mode:`:

- answer callback
- if gmail → same summary path as `start_gmail`
- if paste → open paste session for that chat/period
- edit the picker message to show the chosen mode

Update help text in `cmd_import` error and the bot’s command help string to mention `gmail` / `paste`.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. pytest tests/test_import_mode_callback.py tests/test_finance_import.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/telegram_bot.py tests/test_import_mode_callback.py
git commit -m "feat: add /import mode picker for Gmail vs paste"
```

---

### Task 7: Docs and operator notes

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Test: none (doc-only); verify by reading the sections

**Interfaces:**
- Documents dual `/import`, Gmail+Calendar OAuth, re-login per account, Calendar Pribadi-only.

- [ ] **Step 1: Update `.env.example` Google section**

Replace “Gmail OAuth sudah dihapus” with:

```env
# --- Google OAuth (Gmail finance + Calendar Pribadi) ---
# Enable Gmail API + Google Calendar API on the OAuth client.
GOOGLE_CLIENT_SECRETS=/app/credentials/google_client_secret.json
GOOGLE_TOKEN_PATH=/app/credentials/google_token.json
GOOGLE_TOKEN_PATHS=/app/credentials/google_token.json,/app/credentials/google_token_2.json
GOOGLE_ACCOUNT_LABELS=Pribadi,Kerja
GOOGLE_CALENDAR_WRITE_ACCOUNT=Pribadi
GOOGLE_CALENDAR_WRITE=1
```

Update finance intake comment to say Gmail scan + paste dual mode.

- [ ] **Step 2: Update README command + login sections**

Document:

```text
/import                 → pilih Gmail atau Paste
/import gmail 2026-07   → scan Gmail Pribadi+Kerja
/import paste 2026-07   → sesi paste/upload
```

Document re-login:

```bash
GOOGLE_TOKEN_PATH=.../google_token.json python -m scripts.google_login
GOOGLE_TOKEN_PATH=.../google_token_2.json python -m scripts.google_login
```

Note: after scope change, old tokens must be re-authorized. Prefer OAuth consent **In production** to avoid 7-day refresh expiry.

- [ ] **Step 3: Sanity grep**

Run: `rg -n "Gmail OAuth sudah dihapus|Import Gmail sudah ditutup" README.md .env.example core/agent.py || true`

Expected: no stale “removed” claims for the restored path (agent scan_email message may still point to `/import`).

- [ ] **Step 4: Commit**

```bash
git add .env.example README.md
git commit -m "docs: document Gmail finance import dual mode"
```

---

### Task 8: Full verification

**Files:**
- None new

- [ ] **Step 1: Run the focused suite**

Run:

```bash
PYTHONPATH=. pytest \
  tests/test_google_auth_scopes.py \
  tests/test_gmail_connector.py \
  tests/test_finance_import.py \
  tests/test_import_mode_callback.py \
  tests/test_agent_gmail_wiring.py \
  tests/test_google_connectors.py \
  tests/test_briefing_nyx.py -q
```

Expected: PASS. If `test_briefing_nyx.py` asserts `"Gmail" not in text`, update that test to allow Gmail in the **status** section only (still forbid inbox-style Gmail content). Adjust the assertion to match the design (status OK, inbox body not present).

- [ ] **Step 2: Run broader regression**

Run: `PYTHONPATH=. pytest -q`

Expected: PASS (fix any newly broken assumptions about import `action=="start"`).

- [ ] **Step 3: Operator checklist (manual)**

1. Enable Gmail API + Calendar API on the Google Cloud project  
2. Re-login Pribadi and Kerja tokens  
3. `/status` shows Gmail ✅ when tokens valid  
4. `/import` → buttons → Gmail for current month stages mail  
5. `/import paste` still accepts pasted text  

- [ ] **Step 4: Final commit if test fixes remain**

```bash
git add -A
git commit -m "test: align briefing/status expectations with Gmail restore"
```

---

## Spec coverage check

| Spec requirement | Task |
|---|---|
| Restore Gmail connector Pribadi+Kerja | 2, 5 |
| Dual `/import` + mode picker | 3, 6 |
| Keep paste | 3, 6 |
| Calendar Pribadi unchanged | 1 (scopes only), 5 (no gcal edits) |
| Account-qualified IDs | 2 |
| Skip failed account | 2 |
| `import_period` stages via Gmail | 4 |
| Docs + re-login | 7 |
| No inbox in `/brief` | 5, 8 |
| Out of scope SearXNG | — (not planned) |
