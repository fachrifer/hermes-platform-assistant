# Historical Finance Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import finance emails from 2026 into monthly SQLite staging batches, allow safe review and editing, and commit only explicitly confirmed batches to Money Manager.

**Architecture:** Keep `/scan` unchanged for daily imports. Add a `FinanceImportService` that owns period parsing, Gmail pagination, extraction, monthly staging, review formatting, field edits, and batch commits. Extend the existing `Database` with idempotent batch/staging tables and expose the service through `/import`, `/batch`, and `/edit` Telegram commands with a two-step commit confirmation.

**Tech Stack:** Python 3.11, SQLite, Gmail API, Gemini, Realbyte Money Manager MCP, python-telegram-bot, pytest.

## Global Constraints

- Import only creates or updates staging data; it does not call Money Manager MCP.
- Every staged transaction remains editable before commit.
- Possible duplicates remain in staging and are flagged, never discarded automatically.
- Source email IDs are idempotent across repeated imports.
- Only `/batch konfirmasi YYYY-MM` may call Money Manager MCP.
- Email status becomes `recorded` only after the corresponding MCP call succeeds.
- Partial MCP failures remain retryable and do not prevent other transactions from processing.
- Invalid or incomplete commands explain purpose, syntax, examples, and side effects.

---

### Task 1: Gmail Historical Range and Pagination

**Files:**
- Modify: `connectors/gmail.py` near `_build_finance_query` and `_scan_finance_sync`
- Test: `tests/test_gmail_import_range.py`

**Interfaces:**
- Add `GmailConnector.scan_finance_range(start: date, end: date, max_results: int = 100) -> list[dict]`.
- Add `_build_finance_range_query(start: date, end: date) -> str`.
- Returned items retain the existing `id`, `account`, `from`, `subject`, `date`, and `body` fields.

- [ ] **Step 1: Write tests for inclusive Gmail query construction**

```python
from datetime import date

from connectors.gmail import GmailConnector


def test_finance_range_query_is_inclusive_by_gmail_date():
    connector = GmailConnector()

    query = connector._build_finance_range_query(date(2026, 1, 1), date(2026, 1, 31))

    assert "after:2025/12/31" in query
    assert "before:2026/02/01" in query


def test_finance_range_query_keeps_sender_and_keyword_filters(monkeypatch):
    monkeypatch.setenv("FINANCE_EMAIL_SENDERS", "bank.example")
    monkeypatch.setenv("FINANCE_EMAIL_KEYWORDS", "transfer")
    connector = GmailConnector()

    query = connector._build_finance_range_query(date(2026, 1, 1), date(2026, 1, 31))

    assert "from:(bank.example)" in query
    assert '"transfer"' in query
```

- [ ] **Step 2: Run the focused tests and confirm they fail because the range helper is absent**

Run: `PYTHONPATH=. pytest tests/test_gmail_import_range.py -q`

Expected: FAIL with `AttributeError` for `_build_finance_range_query`.

- [ ] **Step 3: Implement the date-range query**

Use Gmail's exclusive `after` and `before` operators. The end date must be advanced by one day so `/import 2026-01` includes January 31. Reuse `settings.finance_senders_list` and `settings.finance_keywords_list` exactly as the daily scanner does.

- [ ] **Step 4: Add paginated range scanning**

Loop through `users().messages().list(..., pageToken=...)` until `nextPageToken` is absent. Fetch each message with `format="full"`, reuse `_extract_body`, and continue after an individual account/API error while logging the account label.

- [ ] **Step 5: Run the focused tests**

Run: `PYTHONPATH=. pytest tests/test_gmail_import_range.py -q`

Expected: PASS.

### Task 2: SQLite Import Batches and Staged Transactions

**Files:**
- Modify: `core/db.py` schema and methods
- Create: `tests/test_import_db.py`

**Interfaces:**
- `Database.get_or_create_import_batch(period: str) -> dict`.
- `Database.list_import_batches() -> list[dict]`.
- `Database.get_import_batch(period: str) -> dict | None`.
- `Database.stage_transaction(batch_id: int, transaction: dict) -> int | None`.
- `Database.list_staged_transactions(batch_id: int) -> list[dict]`.
- `Database.update_staged_transaction(transaction_id: int, fields: dict) -> None`.
- `Database.set_staged_status(transaction_id: int, status: str, commit_error: str = "") -> None`.

- [ ] **Step 1: Write tests for batch creation and email idempotency**

```python
from core.db import Database


def test_staging_reuses_period_and_rejects_duplicate_email(tmp_path):
    db = Database(str(tmp_path / "state.db"))
    transaction = {
        "source_email_id": "Pribadi:abc",
        "account": "Pribadi",
        "transaction_date": "2026-01-15",
        "amount": 125000,
        "type": "expense",
        "category": "Makanan",
        "merchant": "Toko",
        "description": "Makan",
        "needs_review": 0,
        "possible_duplicate": 0,
    }

    first = db.get_or_create_import_batch("2026-01")
    second = db.get_or_create_import_batch("2026-01")

    assert first["id"] == second["id"]
    assert db.stage_transaction(first["id"], transaction) is not None
    assert db.stage_transaction(first["id"], transaction) is None
    assert len(db.list_staged_transactions(first["id"])) == 1
```

- [ ] **Step 2: Run the database test and confirm it fails because the schema/methods are absent**

Run: `PYTHONPATH=. pytest tests/test_import_db.py -q`

Expected: FAIL because `import_batches` and `stage_transaction` do not exist.

- [ ] **Step 3: Add idempotent staging schema**

Create `import_batches` with a unique `period`. Create `staged_transactions` with a unique `source_email_id`, foreign key `batch_id`, all editable fields, uncertainty flags, status, and `commit_error`. Add indexes on `batch_id`, `status`, and `source_email_id`.

- [ ] **Step 4: Implement database CRUD and validation boundaries**

Keep SQLite locking consistent with existing `execute`/`query`. `stage_transaction` must use `INSERT OR IGNORE` and return `None` for an existing email. `update_staged_transaction` updates only the six supported editable fields and never changes `source_email_id` or `batch_id`.

- [ ] **Step 5: Add tests for updates and batch statuses**

```python
def test_update_staged_transaction_changes_only_editable_fields(tmp_path):
    db = Database(str(tmp_path / "state.db"))
    batch = db.get_or_create_import_batch("2026-01")
    tx_id = db.stage_transaction(batch["id"], {
        "source_email_id": "Kerja:x",
        "account": "Kerja",
        "transaction_date": "2026-01-03",
        "amount": 10,
        "type": "expense",
        "category": "Lainnya",
        "merchant": "X",
        "description": "X",
        "needs_review": 1,
        "possible_duplicate": 1,
    })

    db.update_staged_transaction(tx_id, {"amount": 20, "category": "Makanan"})
    row = db.list_staged_transactions(batch["id"])[0]

    assert row["amount"] == 20
    assert row["category"] == "Makanan"
    assert row["source_email_id"] == "Kerja:x"
```

- [ ] **Step 6: Run database tests**

Run: `PYTHONPATH=. pytest tests/test_import_db.py -q`

Expected: PASS.

### Task 3: Import Service and Monthly Review Data

**Files:**
- Create: `core/finance_import.py`
- Modify: `core/agent.py` to instantiate and expose the service
- Create: `tests/test_finance_import.py`

**Interfaces:**
- `async FinanceImportService.import_period(period: str) -> dict`.
- `FinanceImportService.list_batches() -> list[dict]`.
- `FinanceImportService.get_batch(period: str) -> dict`.
- `FinanceImportService.edit_transaction(transaction_id: int, field: str, value: str) -> str`.
- `FinanceImportService.preview_commit(period: str) -> str`.
- `async FinanceImportService.confirm_commit(period: str) -> str`.
- `FinanceImportService.skip_period(period: str) -> str`.
- `FinanceImportService.confirm_skip(period: str) -> str`.

- [ ] **Step 1: Write tests for period parsing and monthly grouping**

```python
from core.finance_import import parse_import_period, month_period


def test_parse_year_and_month_periods():
    assert parse_import_period("2026") == ("2026-01-01", "2026-12-31")
    assert parse_import_period("2026-02") == ("2026-02-01", "2026-02-28")


def test_month_period_uses_transaction_date():
    assert month_period("Sat, 15 Jan 2026 10:00:00 +0700") == "2026-01"
```

- [ ] **Step 2: Run the tests and confirm they fail because the service is absent**

Run: `PYTHONPATH=. pytest tests/test_finance_import.py -q`

Expected: collection failure because `core.finance_import` does not exist.

- [ ] **Step 3: Implement period parsing and Gmail import**

Accept only `YYYY` or `YYYY-MM`; reject other formats with a usage message. `import_period` is async because Gmail and Gemini calls are async at the service boundary. For each email, skip `recorded_emails` and existing staged source IDs, call `extract_transaction`, normalize the transaction date, and create the corresponding monthly batch. If Money Manager is available, await `get_init_data()` once per import and pass the returned category list into Gemini extraction.

- [ ] **Step 4: Implement duplicate candidate detection**

Before staging a transaction, compare normalized transaction date, integer amount, type, and case-folded merchant against existing staged transactions from the same import. Set `possible_duplicate=1` for matches but still insert the transaction.

- [ ] **Step 5: Implement monthly summaries and review formatting**

Return transaction count, expense total, income total, `needs_review` count, and `possible_duplicate` count. Format `/batch YYYY-MM` as read-only output with explicit next commands and a warning that no MCP call occurs during review.

- [ ] **Step 6: Implement field editing and validation**

Map `tanggal`, `nominal`, `tipe`, `kategori`, `merchant`, and `deskripsi` to database fields. Validate ISO dates, positive integer amounts, `expense|income`, and non-empty category/merchant/description values where applicable. Return an explanatory usage response for unknown IDs or fields.

- [ ] **Step 7: Run service tests**

Run: `PYTHONPATH=. pytest tests/test_finance_import.py -q`

Expected: PASS.

### Task 4: Safe Batch Commit and Telegram Commands

**Files:**
- Modify: `core/finance_import.py` commit and skip methods
- Modify: `core/telegram_bot.py` command registration and handlers
- Modify: `core/agent.py` import service delegation
- Create: `tests/test_import_commands.py`

**Interfaces:**
- Telegram registers `/import`, `/batch`, and `/edit`.
- `/batch catat YYYY-MM` calls only `preview_commit`.
- `/batch konfirmasi YYYY-MM` calls only `confirm_commit`.
- `/batch lewati YYYY-MM` calls only a warning renderer.
- `/batch konfirmasi-lewati YYYY-MM` calls `confirm_skip` and changes the batch to `skipped`.

- [ ] **Step 1: Write tests that prove preview does not call MCP**

```python
import pytest


@pytest.mark.asyncio
async def test_commit_preview_is_read_only():
    calls = []
    service = type("Import", (), {
        "preview_commit": lambda self, period: "preview",
        "confirm_commit": lambda self, period: calls.append(period),
    })()

    assert service.preview_commit("2026-01") == "preview"
    assert calls == []
```

- [ ] **Step 2: Run the command test and confirm the safety behavior is not implemented**

Run: `PYTHONPATH=. pytest tests/test_import_commands.py -q`

Expected: FAIL until the command/service behavior is implemented.

- [ ] **Step 3: Implement two-step confirmation**

`preview_commit` must include period, count, totals, uncertain count, duplicate count, and the exact confirmation command. `confirm_commit` is async and must await `transaction_create` for each transaction, mark successes `committed`, mark source emails `recorded` only after success, preserve errors, and leave the batch `approved` if failures remain.

- [ ] **Step 4: Implement skip confirmation**

`/batch lewati YYYY-MM` returns a warning and the exact confirmation command. Only `/batch konfirmasi-lewati YYYY-MM` calls `confirm_skip`, marks the batch `skipped`, and never calls MCP.

- [ ] **Step 5: Implement command guidance**

No-argument and invalid forms must explain the command purpose, valid syntax, example, and side effect. `/import` must explicitly say it only stages; `/batch` must say viewing is read-only; `/edit` must list all six editable fields.

- [ ] **Step 6: Run command tests**

Run: `PYTHONPATH=. pytest tests/test_import_commands.py -q`

Expected: PASS.

### Task 5: Documentation and Full Verification

**Files:**
- Modify: `README.md` historical import, staging, and command sections
- Test: full suite

- [ ] **Step 1: Document the import workflow**

Add examples for `/import 2026`, `/import 2026-01`, `/batch`, `/batch 2026-01`, `/edit`, `/batch catat`, `/batch konfirmasi`, `/batch lewati`, and `/batch konfirmasi-lewati`. Clearly state that import/review/edit do not call MCP and only explicit confirmation sends data to Money Manager.

- [ ] **Step 2: Run all tests**

Run: `PYTHONPATH=. pytest -q`

Expected: all tests pass with exit code 0.

- [ ] **Step 3: Rebuild and verify the container**

Run: `docker compose up --build -d`

Run: `docker compose ps`

Expected: `hermes` reports `healthy`.
