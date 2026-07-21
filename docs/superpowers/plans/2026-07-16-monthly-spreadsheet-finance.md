# Monthly Spreadsheet Finance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct Money Manager MCP writes with monthly Realbyte-compatible spreadsheet export and spreadsheet-backed financial reporting, while making Hermes deployable as one persistent cloud service.

**Architecture:** Gmail transactions are extracted with Gemini and staged in SQLite. A spreadsheet service validates categories, exports reviewed monthly batches as XLSX plus TSV, and reads the configured history workbook plus exported workbooks for reports. Hermes runs as one Docker service in the cloud with persistent `/app/data` and `/app/credentials` mounts; Money Manager is never contacted by the server.

**Tech Stack:** Python 3.11, FastAPI, python-telegram-bot, SQLite, Google Gmail/Calendar APIs, Gemini, `openpyxl`, Docker Compose, persistent cloud volumes.

## Global Constraints

- Use the exact Realbyte columns: `Date`, `Account`, `Category`, `Subcategory`, `Note`, `Amount`, `Income/Expense`, `Description`.
- Export dates as `mm/dd/yyyy` and transaction amounts as positive numbers.
- Default account is `Cash`.
- Export must not mark email as `recorded`; manual import happens outside Hermes.
- Existing staging and email-idempotency data must remain readable.
- `MS_OUTLOOK_ENABLED=0` is the default.
- Remove `mcp` and Node.js from the Hermes runtime.
- Reference workbooks are mounted private data and must not be committed or copied into the image.
- Cloud deployment is one persistent Hermes container with persistent data and credentials; Telegram uses polling, so no public Telegram webhook is required.

---

### Task 1: Add Spreadsheet Dependencies and Configuration

**Files:**
- Modify: `requirements.txt`
- Modify: `config/settings.py`
- Modify: `.env.example`
- Modify: `.env` locally only if needed; never commit it
- Test: `tests/test_spreadsheet_settings.py`

**Interfaces:**
- Produces `settings.finance_account: str`, `settings.finance_category_reference: str`,
  `settings.finance_history_workbook: str`, `settings.finance_export_dir: str`, and
  `settings.ms_outlook_enabled: bool`.

- [ ] **Step 1: Write failing settings tests**

```python
def test_spreadsheet_settings_defaults(monkeypatch):
    monkeypatch.delenv("FINANCE_ACCOUNT", raising=False)
    monkeypatch.delenv("FINANCE_EXPORT_DIR", raising=False)
    from config.settings import Settings

    value = Settings()

    assert value.finance_account == "Cash"
    assert value.finance_export_dir == "/app/data/finance"


def test_outlook_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MS_OUTLOOK_ENABLED", raising=False)
    from config.settings import Settings

    assert Settings().ms_outlook_enabled is False
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `pytest tests/test_spreadsheet_settings.py -q`

Expected: FAIL because the new settings properties do not exist.

- [ ] **Step 3: Add `openpyxl==3.1.5` and settings fields**

Add the dependency to `requirements.txt`. In `Settings`, add:

```python
finance_account: str = field(default_factory=lambda: _get("FINANCE_ACCOUNT", "Cash"))
finance_category_reference: str = field(
    default_factory=lambda: _get(
        "FINANCE_CATEGORY_REFERENCE", "/app/data/reference/Kategori Money Manager.xlsx"
    )
)
finance_history_workbook: str = field(
    default_factory=lambda: _get(
        "FINANCE_HISTORY_WORKBOOK", "/app/data/reference/MoneyManager-2025.xlsx"
    )
)
finance_export_dir: str = field(
    default_factory=lambda: _get("FINANCE_EXPORT_DIR", "/app/data/finance")
)
ms_outlook_enabled: bool = field(
    default_factory=lambda: _get("MS_OUTLOOK_ENABLED", "0") == "1"
)
```

Extend `ensure_dirs()` with `settings.finance_export_dir` and the parent paths of
the two optional workbook settings.

- [ ] **Step 4: Document the variables**

Add the five variables to `.env.example`, including that the reference files are
mounted private files and that `MS_OUTLOOK_ENABLED=0` disables Outlook.

- [ ] **Step 5: Run the focused tests**

Run: `pytest tests/test_spreadsheet_settings.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the isolated change**

```bash
git add requirements.txt config/settings.py .env.example tests/test_spreadsheet_settings.py
git commit -m "feat: configure spreadsheet finance storage"
```

---

### Task 2: Implement Workbook Parsing, Export, and Reporting

**Files:**
- Create: `core/spreadsheet_finance.py`
- Test: `tests/test_spreadsheet_finance.py`
- Test fixture: use the existing root workbooks only as local test inputs; do not add private copies to Git

**Interfaces:**
- `CategoryReference(path).pairs() -> set[tuple[str, str]]`
- `SpreadsheetFinanceService.export_batch(period, rows) -> ExportResult`
- `SpreadsheetFinanceService.month_summary(period) -> dict`
- `SpreadsheetFinanceService.report_prompt(period, comparison_period=None) -> str`

- [ ] **Step 1: Write parser and export failing tests**

```python
def test_category_reference_reads_category_subcategory_pairs(tmp_path):
    path = make_category_workbook(tmp_path / "categories.xlsx")
    reference = CategoryReference(str(path))

    assert ("Makanan", "Restoran") in reference.pairs()
    assert ("Gaji", "Gaji Pokok") in reference.pairs()


def test_export_writes_realbyte_columns_and_date_format(tmp_path):
    service = SpreadsheetFinanceService(
        export_dir=str(tmp_path), category_reference=None, history_workbook=None
    )
    result = service.export_batch("2026-07", [{
        "transaction_date": "2026-07-16", "account": "Cash",
        "category": "Makanan", "subcategory": "Restoran", "merchant": "Kedai",
        "amount": 25000, "type": "expense", "description": "Makan",
    }])

    assert result.xlsx_path.endswith("2026-07.xlsx")
    assert result.tsv_path.endswith("2026-07.tsv")
    assert read_xlsx_rows(result.xlsx_path)[0] == [
        "Date", "Account", "Category", "Subcategory", "Note", "Amount",
        "Income/Expense", "Description",
    ]
    assert read_xlsx_rows(result.xlsx_path)[1][0] == "07/16/2026"
    assert read_tsv(result.tsv_path).splitlines()[1].startswith("07/16/2026\tCash\tMakanan")
```

Add test helpers that create a small workbook with `openpyxl`; do not depend on
the user's private reference workbook for deterministic unit tests.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `pytest tests/test_spreadsheet_finance.py -q`

Expected: FAIL because the service and result type do not exist.

- [ ] **Step 3: Implement category parsing**

Parse all worksheet cells from the category reference. Treat rows whose first
cell is `Kategori Pengeluaran` or `Kategori Pendapatan` as section headers. For
each subsequent data row, use column A as the category and every non-empty cell
from column B onward as a subcategory; when no column B+ value exists, pair the
category with `-`. Preserve `-` as the no-subcategory marker. Normalize only for
comparison and retain the workbook spelling for output. This matches the
provided workbook, where a row such as `Makanan | Restoran | Cafe | Grocery`
defines three valid pairs for `Makanan`.

- [ ] **Step 4: Implement XLSX and TSV export**

Use a constant `REALBYTE_COLUMNS`. Map staging rows to the eight columns:

```python
[
    date.strftime("%m/%d/%Y"), row["account"] or settings.finance_account,
    row["category"], row.get("subcategory", "-"), row.get("merchant", ""),
    int(row["amount"]), "Income" if row["type"] == "income" else "Expense",
    row.get("description", ""),
]
```

Write the XLSX with a header row and freeze panes at `A2`. Write UTF-8 TSV with
`csv.writer(tsv_file, delimiter="\t", lineterminator="\n")`. Write to temporary
files in the same directory and atomically replace the target files only after
both formats succeed.

- [ ] **Step 5: Implement history/month reader and aggregation**

Read the first worksheet of the history workbook and every `YYYY-MM.xlsx` in the
export directory. Ignore rows whose first cell is not a valid date. Normalize
`Income`/`Expense` case-insensitively and aggregate by month, category, and
subcategory. Return:

```python
{
    "period": "2026-07", "income_total": 0, "expense_total": 0,
    "by_category": {"Makanan": 25000},
    "by_subcategory": {"Makanan / Restoran": 25000},
    "largest": [{"date": "07/16/2026", "amount": 25000, "category": "Makanan"}],
    "rows": [{"date": "07/16/2026", "account": "Cash", "category": "Makanan",
              "subcategory": "Restoran", "amount": 25000, "kind": "Expense"}],
}
```

Missing or malformed optional workbooks return an unavailable result with an
explicit error string instead of raising during application startup.

- [ ] **Step 6: Implement Gemini report prompt generation**

Build a bounded prompt from the normalized summary, asking for a concise Bahasa
Indonesia report. Never send more than the configured summary rows and do not
include source email bodies or credentials.

- [ ] **Step 7: Run focused tests**

Run: `pytest tests/test_spreadsheet_finance.py -q`

Expected: PASS.

- [ ] **Step 8: Commit the isolated change**

```bash
git add core/spreadsheet_finance.py tests/test_spreadsheet_finance.py
git commit -m "feat: export and report spreadsheet finance data"
```

---

### Task 3: Convert Staging to Monthly Export Workflow

**Files:**
- Modify: `core/db.py:56-85, 130-195`
- Modify: `core/finance_import.py:50-243`
- Test: `tests/test_finance_import.py`
- Test: `tests/test_spreadsheet_finance.py`

**Interfaces:**
- `Database.set_import_batch_status(period, status)` accepts `exported`.
- `FinanceImportService.export_period(period) -> ExportResult`.
- `FinanceImportService.confirm_commit()` is removed.

- [ ] **Step 1: Write failing workflow tests**

```python
@pytest.mark.asyncio
async def test_export_period_does_not_call_money_and_marks_exported(tmp_path):
    db = Database(str(tmp_path / "state.db"))
    batch = db.get_or_create_import_batch("2026-07")
    db.stage_transaction(batch["id"], {
        "source_email_id": "mail-1", "account": "Cash",
        "transaction_date": "2026-07-16", "amount": 25000,
        "type": "expense", "category": "Makanan", "merchant": "Kedai",
        "description": "Makan",
    })
    money = FailingMoney()
    spreadsheet_service = SpreadsheetFinanceService(
        export_dir=str(tmp_path / "exports"), category_reference=None, history_workbook=None
    )
    service = FinanceImportService(
        db, None, None, money, spreadsheet_service=spreadsheet_service
    )

    result = await service.export_period("2026-07")

    assert result.xlsx_path
    assert db.get_import_batch("2026-07")["status"] == "exported"
    assert not money.called
```

Add tests for unknown category/missing account rejection and for a second export
requiring an explicit `force=True` argument.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `pytest tests/test_finance_import.py -q`

Expected: FAIL because `export_period` and the new dependency injection do not
exist.

- [ ] **Step 3: Remove Money Manager dependency from import staging**

Change `FinanceImportService` to accept `spreadsheet_service` and make the old
`money` argument optional only while migrating tests. Stop calling
`get_init_data()`. Load category text from the category reference and pass the
formatted pairs to `extract_transaction`.

- [ ] **Step 4: Add subcategory/account fields to staging**

Add SQLite columns with additive migrations:

```sql
ALTER TABLE staged_transactions ADD COLUMN subcategory TEXT NOT NULL DEFAULT '-';
```

Set empty accounts to `settings.finance_account` at staging time. Extend edit
mapping with `akun` and `subkategori`, and validate the date/type/amount after
each edit.

- [ ] **Step 5: Implement export-only completion**

Reject rows with `needs_review`, empty account, invalid type, non-positive
amount, or category/subcategory pairs not in the reference. Call
`SpreadsheetFinanceService.export_batch()`, update the batch to `exported`, and
leave `recorded_emails` unchanged. Remove `preview_commit`, `confirm_commit`,
and direct MCP payload creation.

- [ ] **Step 6: Run focused tests**

Run: `pytest tests/test_finance_import.py tests/test_import_db.py -q`

Expected: PASS, including the legacy idempotency tests and the new export tests.

- [ ] **Step 7: Commit the isolated change**

```bash
git add core/db.py core/finance_import.py tests/test_finance_import.py tests/test_import_db.py
git commit -m "feat: make finance batches export-only"
```

---

### Task 4: Remove MCP Money Manager Behavior and Add Spreadsheet Reports

**Files:**
- Modify: `core/agent.py`
- Modify: `core/briefing.py`
- Modify: `core/llm_client.py`
- Delete: `connectors/money_manager.py`
- Test: `tests/test_agent_finance.py`
- Test: `tests/test_briefing.py`

**Interfaces:**
- `HermesAgent.finance_report(period, comparison_period=None) -> str`.
- `HermesAgent.money` no longer exists.
- `HermesAgent.connectors` contains Gmail, Google Calendar, manual agenda, and
  any other non-MCP connectors only.

- [ ] **Step 1: Write failing tests**

```python
def test_agent_has_no_money_manager_connector():
    agent = HermesAgent.__new__(HermesAgent)
    assert not hasattr(agent, "money")


@pytest.mark.asyncio
async def test_finance_report_uses_spreadsheet_service(agent):
    agent.spreadsheet_finance = FakeSpreadsheetService("July expense Rp25.000")
    assert await agent.finance_report("2026-07") == "July expense Rp25.000"
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `pytest tests/test_agent_finance.py tests/test_briefing.py -q`

Expected: FAIL because existing agent methods still instantiate and call Money
Manager.

- [ ] **Step 3: Remove direct money actions**

Delete `_do_add_expense`, direct `record_scanned_transaction`, finance query
tool calls, Money Manager advice fetches, `money_tools_hint`, and the money
connector import/registry entry. Natural-language finance requests should route
to the spreadsheet report or return a clear message that manual spreadsheet
export is required for new entries.

- [ ] **Step 4: Wire spreadsheet extraction and reporting**

Instantiate `CategoryReference` and `SpreadsheetFinanceService` in the agent.
Use the category reference in `scan_email_transactions`; confirmed scanned
transactions go to the current month's staging batch instead of Money Manager.
Add `finance_report()` to summarize the configured spreadsheet data through
Gemini.

- [ ] **Step 5: Update briefing**

Replace the Money Manager health branch with a spreadsheet report branch. A
missing history workbook should produce `Spreadsheet finance belum tersedia`
without making `/brief` fail.

- [ ] **Step 6: Run focused tests**

Run: `pytest tests/test_agent_finance.py tests/test_briefing.py tests/test_finance_import.py -q`

Expected: PASS with no MCP import or Money Manager call.

- [ ] **Step 7: Commit the isolated change**

```bash
git add core/agent.py core/briefing.py core/llm_client.py tests/test_agent_finance.py tests/test_briefing.py
git rm connectors/money_manager.py
git commit -m "refactor: replace Money Manager calls with spreadsheet finance"
```

---

### Task 5: Update Telegram Commands for Monthly Export and Reports

**Files:**
- Modify: `core/telegram_bot.py`
- Modify: `README.md`
- Modify: `tests/test_import_commands.py`
- Create: `tests/test_monthly_export_commands.py`

**Interfaces:**
- Register `/export` and `/finance`.
- `/batch export YYYY-MM` calls `FinanceImportService.export_period()`.
- `/batch konfirmasi` and `/setmoney` are no longer registered.

- [ ] **Step 1: Write failing command tests**

```python
def test_monthly_commands_are_registered():
    assert hasattr(TelegramInterface, "cmd_export")
    assert hasattr(TelegramInterface, "cmd_finance")


def test_help_describes_manual_monthly_import():
    assert "export" in get_help_text().lower()
    assert "Money Manager" not in get_help_text()
```

- [ ] **Step 2: Implement command changes**

Change `/help` to show:

```text
/import YYYY-MM — ambil email ke staging
/batch YYYY-MM — review transaksi
/export YYYY-MM — buat XLSX dan TSV untuk import manual
/finance YYYY-MM — laporan dari spreadsheet
```

Add `cmd_export` with exact usage validation and a reply containing both output
paths and the number of rows. Add `cmd_finance` with period validation and a
typing indicator before calling `agent.finance_report()`.

- [ ] **Step 3: Update batch output**

For an `exported` batch, show the two generated filenames and remove all
`catat`/`konfirmasi`/`konfirmasi-lewati` branches. Preserve `/batch` review and
`/edit` behavior.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_import_commands.py tests/test_monthly_export_commands.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the isolated change**

```bash
git add core/telegram_bot.py README.md tests/test_import_commands.py tests/test_monthly_export_commands.py
git commit -m "feat: add monthly spreadsheet export commands"
```

---

### Task 6: Disable Outlook by Default

**Files:**
- Modify: `core/agent.py`
- Modify: `connectors/outlook.py`
- Modify: `scripts/ms_login.py`
- Modify: `config/settings.py`
- Test: `tests/test_outlook_settings.py`

- [ ] **Step 1: Write failing tests**

```python
def test_outlook_is_unavailable_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ms_outlook_enabled", False)
    assert OutlookConnector().is_available() is False


def test_disabled_outlook_is_not_registered(monkeypatch):
    monkeypatch.setattr(settings, "ms_outlook_enabled", False)
    agent = HermesAgent()
    assert all(conn.name != "Outlook" for conn in agent.connectors)
```

- [ ] **Step 2: Implement the flag**

Make `OutlookConnector.is_available()` return `False` unless the flag is true.
Only instantiate/register it in `HermesAgent` when enabled. Guard the login
script with a clear disabled message if `MS_OUTLOOK_ENABLED` is not `1`.

- [ ] **Step 3: Verify tests**

Run: `pytest tests/test_outlook_settings.py -q`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add core/agent.py connectors/outlook.py scripts/ms_login.py config/settings.py tests/test_outlook_settings.py
git commit -m "feat: disable Outlook integration by default"
```

---

### Task 7: Make the Container Cloud-Ready

**Files:**
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `.dockerignore`
- Modify: `README.md`
- Create: `docker-compose.cloud.yml`
- Create: `deploy/cloud.env.example`
- Test: `tests/test_cloud_config.py`

**Interfaces:**
- The production image starts only Python dependencies and has no Node.js or
  MCP package.
- Cloud compose persists `data/` and `credentials/`, reads an external env
  file, binds health port to localhost by default, and restarts automatically.

- [ ] **Step 1: Write configuration tests**

```python
def test_cloud_compose_persists_required_paths():
    text = Path("docker-compose.cloud.yml").read_text()
    assert "/app/data" in text
    assert "/app/credentials" in text
    assert "restart: unless-stopped" in text
```

- [ ] **Step 2: Remove Node.js/MCP from Docker**

Delete the NodeSource setup and `mcp` dependency. Keep `curl` for the existing
healthcheck. Copy only application Python files and create `/app/data`,
`/app/credentials`, and `/app/data/reference`.

- [ ] **Step 3: Add cloud compose**

Create a compose file with one service:

```yaml
services:
  hermes:
    build: .
    env_file: deploy/cloud.env
    ports:
      - "127.0.0.1:${HERMES_PORT:-8000}:8000"
    volumes:
      - hermes-data:/app/data
      - hermes-credentials:/app/credentials
      - ${FINANCE_REFERENCE_DIR:?set FINANCE_REFERENCE_DIR}:/app/data/reference:ro
    restart: unless-stopped
```

Document that `deploy/cloud.env` contains secrets and is not committed, while
the host reference directory contains the two private workbooks. If credentials
are supplied by a secret manager instead, mount them read-only at the same
paths.

- [ ] **Step 4: Add cloud env template and operational docs**

Document the commands:

```bash
mkdir -p /opt/hermes/reference /opt/hermes/data /opt/hermes/credentials
cp "Kategori Money Manager.xlsx" /opt/hermes/reference/
cp "MoneyManager-2025.xlsx" /opt/hermes/reference/
cp deploy/cloud.env.example deploy/cloud.env
docker compose -f docker-compose.cloud.yml up -d --build
docker compose -f docker-compose.cloud.yml logs -f hermes
curl http://127.0.0.1:8000/health
```

Include backup instructions for `/app/data` and credentials, single-instance
SQLite operation, Telegram polling requirements, and a warning not to expose
port 8000 publicly without an authenticated reverse proxy.

- [ ] **Step 5: Run config and build checks**

Run: `pytest tests/test_cloud_config.py -q`

Run: `docker compose -f docker-compose.cloud.yml config`

Expected: PASS and valid rendered Compose configuration. If Docker is not
available, record that limitation instead of claiming a successful build.

- [ ] **Step 6: Commit the deployment change**

```bash
git add Dockerfile docker-compose.yml docker-compose.cloud.yml .dockerignore README.md deploy/cloud.env.example tests/test_cloud_config.py
git commit -m "ops: prepare Hermes for cloud deployment"
```

---

### Task 8: Full Verification and Migration Notes

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-16-monthly-spreadsheet-finance-design.md` only if implementation decisions require clarification
- Test: all `tests/`

- [ ] **Step 1: Run the complete test suite**

Run: `pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Verify no runtime MCP references remain**

Run: `grep -R "from mcp\|import mcp\|transaction_create\|setmoney\|MoneyManagerConnector" --exclude-dir=.venv --exclude-dir=.pytest_cache .`

Expected: no application/runtime matches; historical design documents may be
updated or explicitly labeled as superseded.

- [ ] **Step 3: Test against the provided workbooks locally**

Run a one-shot Python check using the configured paths and verify:

- category pairs load;
- `MoneyManager-2025.xlsx` produces a non-empty 2025 summary;
- a generated monthly workbook has eight columns;
- TSV starts with the exact Realbyte header.

- [ ] **Step 4: Build and smoke-test the cloud image**

Run: `docker compose -f docker-compose.cloud.yml build`

Run: `docker compose -f docker-compose.cloud.yml up -d`

Run: `curl http://127.0.0.1:8000/health`

Expected: HTTP 200 with connector health and no Money Manager entry.

- [ ] **Step 5: Commit final documentation/migration notes**

```bash
git add README.md docs/superpowers/specs/2026-07-16-monthly-spreadsheet-finance-design.md
git commit -m "docs: document monthly spreadsheet deployment"
```
