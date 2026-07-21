# Monthly Spreadsheet Finance Design

## Goal

Remove the Money Manager MCP integration from Hermes. Hermes will collect and
classify finance transactions from email, stage them for monthly review, export
files that can be imported manually into Realbyte Money Manager, and read the
spreadsheet history to produce financial reports.

Outlook remains in the repository but is disabled by default.

## Scope

### In scope

- Remove Money Manager MCP runtime and application integration.
- Keep monthly finance-email import and SQLite staging.
- Validate extracted categories against `Kategori Money Manager.xlsx`.
- Use the Realbyte-compatible columns:

  `Date`, `Account`, `Category`, `Subcategory`, `Note`, `Amount`,
  `Income/Expense`, `Description`.

- Export each reviewed month as both `.xlsx` and `.tsv`.
- Read the existing `MoneyManager-2025.xlsx` history and exported monthly files.
- Generate finance summaries from spreadsheet data.
- Disable Outlook through `MS_OUTLOOK_ENABLED=0` by default.

### Out of scope

- Direct writes to Money Manager.
- Money Manager MCP bridge, VPN, tunnel, or home service.
- Automatic upload or import into the Money Manager app.
- Automatic scheduled export; export is initiated by a monthly Telegram command.
- Editing or rewriting the source history workbook.

## Reference Data

- `Kategori Money Manager.xlsx` is the category reference. It contains grouped
  expense and income categories with optional subcategories.
- `MoneyManager-2025.xlsx` is read-only historical data and follows the
  Realbyte import column order. Its observed account value is `Cash`.
- Reference workbooks are local/private data and must not be committed or baked
  into the Docker image. Their paths are configurable through environment
  variables and mounted through the existing data/credentials mechanism.

## Architecture

```text
Gmail -> FinanceImportService -> SQLite staging -> monthly export
                                      |                 |
                                      |                 +-> XLSX for review
                                      |                 +-> TSV for Realbyte import
                                      v
                              SpreadsheetReportService
                                      |
                                      v
                                   Gemini
                                      |
                                   Telegram
```

Hermes remains the only deployed service. The finance spreadsheet is the
system of record for reports after manual review/import. SQLite remains the
staging and idempotency store for email processing.

## Monthly Workflow

1. `/import YYYY-MM` scans finance emails in that month and stages one row per
   source email. Re-running the command remains idempotent.
2. `/batch YYYY-MM` displays counts, totals, uncertain rows, and duplicates.
3. `/edit <id> <field> <value>` changes supported staging fields before export.
4. `/export YYYY-MM` validates rows, writes `YYYY-MM.xlsx` and `YYYY-MM.tsv`,
   and marks the batch as `exported` only after both files are written.
5. The user reviews the XLSX and imports the TSV manually into Money Manager.
6. Reports read the configured history workbook plus exported monthly workbooks.

Export must not mark an email as `recorded` because manual import happens
outside Hermes. A future re-export of an already exported batch requires an
explicit command or confirmation and must not silently duplicate files.

## Spreadsheet Format

Every exported row uses the exact Realbyte order and spelling:

| Column | Source | Rules |
|---|---|---|
| Date | `transaction_date` | `mm/dd/yyyy` in exported files |
| Account | staged/configured | Required; default is `Cash` |
| Category | extracted/edited | Must match the category reference |
| Subcategory | extracted/edited | `-` when no subcategory applies |
| Note | merchant | Human-readable transaction note |
| Amount | staged amount | Positive numeric value |
| Income/Expense | staged type | `Income` or `Expense` |
| Description | staged description | Optional detail |

The XLSX is for review and editing. The TSV uses the same header and row
values, because Realbyte's documented import flow downloads the spreadsheet as
Tab Separated Values before importing it on the phone.

Rows with missing required values or unknown categories are rejected from the
export and reported to the user with transaction IDs. They remain editable in
staging.

## Category Matching

The category workbook is parsed into valid `(category, subcategory)` pairs.
Extraction uses those values as the LLM classification vocabulary. Matching is
case-insensitive for validation but exported spelling preserves the reference
spelling. An empty or `-` subcategory is accepted where the reference category
has no subcategory.

If Gemini cannot determine a valid pair, the row is marked `needs_review` and
is not exportable until edited. The existing history's free-form descriptions
do not become category definitions.

## Reporting

`SpreadsheetReportService` loads rows from the configured history workbook and
the generated monthly export directory. It normalizes dates, amounts, and
income/expense labels, then provides:

- total income and expense for a requested month;
- category and subcategory totals;
- largest transactions;
- month-over-month comparison when both months exist;
- raw normalized rows for Gemini summaries.

Malformed workbooks or missing files produce a clear unavailable message and do
not prevent Telegram or other connectors from starting. Reports identify the
source period and do not include staging rows that have not been exported.

## Configuration

Planned settings:

- `MS_OUTLOOK_ENABLED=0`
- `FINANCE_ACCOUNT=Cash`
- `FINANCE_CATEGORY_REFERENCE=/app/data/reference/Kategori Money Manager.xlsx`
- `FINANCE_HISTORY_WORKBOOK=/app/data/reference/MoneyManager-2025.xlsx`
- `FINANCE_EXPORT_DIR=/app/data/finance`

The category reference and history workbook are mounted files. The defaults
are safe paths inside the container; local deployments may override them.

## Removal and Compatibility

- Remove `mcp` from Python dependencies.
- Remove Node.js installation from the Hermes Docker image.
- Remove Money Manager from the connector registry and all direct tool calls.
- Retain the old SQLite staging tables and existing email idempotency data.
- Existing `committed` staging rows remain readable as historical staging data,
  but no new row can enter `committed` through Hermes.
- Remove or retire `/setmoney`, `/batch konfirmasi`, and related direct-commit
  behavior. `/export` is the only monthly completion action.

## Testing

- Parse category workbook into expected category/subcategory pairs.
- Read history workbook with the expected eight columns.
- Export valid staging rows to XLSX and TSV with exact column order and date
  formatting.
- Reject missing account/category and unknown category pairs.
- Keep duplicate email staging idempotent.
- Mark a batch exported only when both output files succeed.
- Aggregate monthly spreadsheet reports by income, expense, category, and
  subcategory.
- Verify Money Manager MCP is no longer imported or called.
- Verify Outlook is absent from health and agenda when disabled.
