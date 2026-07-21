# Historical Finance Import Design

## Goal

Import financial emails from January 2026 onward, group extracted transactions
by calendar month, allow complete review and editing in a staging area, and only
then commit approved transactions to Money Manager through MCP.

## Commands

- `/import` shows usage and explains that import only creates staging data.
- `/import 2026` imports emails from `2026-01-01` through `2026-12-31`.
- `/import 2026-01` imports one calendar month.
- `/batch` lists available monthly import batches and their statuses.
- `/batch 2026-01` shows a monthly summary and review actions.
- `/edit` shows valid fields and examples without changing anything.
- `/edit <id> <field> <value>` edits one staged transaction field.
- `/batch catat 2026-01` shows a commit preview and asks for confirmation.
- `/batch konfirmasi 2026-01` sends the reviewed batch to Money Manager.
- `/batch lewati 2026-01` shows a warning and asks for confirmation before
   skipping a batch without sending transactions to MCP.
- `/batch konfirmasi-lewati 2026-01` confirms skipping the batch.

The existing `/scan` flow remains for daily review and is not changed into a
historical import command.

## Command Guidance and Safety

Every command has an explanatory response when its syntax is incomplete or
invalid. The response includes the command purpose, valid format, an example,
and whether it changes staging data or Money Manager.

### `/import`

```text
/import 2026
/import 2026-01
```

The command explains that it reads Gmail and creates or updates staging records
only. It does not call Money Manager MCP. Re-running an existing period reports
which emails are already staged or recorded rather than creating duplicates.

### `/batch`

```text
/batch
/batch 2026-01
```

The monthly view explains the number of transactions, totals, items needing
review, possible duplicates, and the available next actions. Viewing a batch is
read-only.

### `/edit`

Without arguments it shows:

```text
/edit <id> tanggal 2026-01-15
/edit <id> nominal 125000
/edit <id> tipe expense
/edit <id> kategori Makanan
/edit <id> merchant Nama Toko
/edit <id> deskripsi Keterangan transaksi
```

It explicitly states that edits affect staging only and do not change Money
Manager until a confirmed commit.

### Commit Confirmation

`/batch catat 2026-01` never commits immediately. It displays a preview with
the month, transaction count, income/expense totals, uncertain items, and
possible duplicates, then instructs the user to run:

```text
/batch konfirmasi 2026-01
```

Only the confirmation command can call Money Manager MCP. The confirmation
response repeats the month and count before processing begins and treats all
remaining `pending` transactions as approved for that commit.

Skipping uses the same two-step safety pattern: `/batch lewati 2026-01` only
shows the warning, while `/batch konfirmasi-lewati 2026-01` changes the batch
status to `skipped`.

## Import Flow

1. Validate the requested year or month and construct an inclusive Gmail date
   range.
2. Fetch all matching finance emails with Gmail pagination, not only the first
   page or a fixed 20-message limit.
3. Ignore email IDs already present in `recorded_emails` or in an existing
   staging record.
4. Extract transaction date, amount, type, category, merchant, and description
   with Gemini.
5. Ground category selection in the categories returned by Money Manager's
   `init_get_data` tool.
6. Store every extracted transaction in a monthly staging batch, including low-
   confidence items and possible duplicates.
7. Mark uncertainty and duplicate candidates for review; never discard them
   automatically.

## Staging Data

### `import_batches`

- `id`
- `period` (`YYYY-MM`)
- `status`: `reviewing`, `approved`, `committed`, or `skipped`
- `created_at`

### `staged_transactions`

- `id`
- `batch_id`
- `source_email_id`
- `account`
- `transaction_date`
- `amount`
- `type` (`expense` or `income`)
- `category`
- `merchant`
- `description`
- `needs_review`
- `possible_duplicate`
- `status`: `pending`, `approved`, `committed`, or `skipped`
- `commit_error`

The source email ID is unique within staging so the same email cannot create a
second staged transaction. A separate duplicate fingerprint based on date,
amount, type, and merchant is used only to flag possible cross-email duplicates.

## Review and Editing

Every staged transaction remains editable before commit. Supported fields:

- `tanggal`
- `nominal`
- `tipe`
- `kategori`
- `merchant`
- `deskripsi`

Editing validates dates, positive amounts, transaction type, and non-empty
categories. Editing clears the relevant uncertainty flag but does not erase the
duplicate warning without explicit review.

## Commit Behavior

When a monthly batch is committed:

1. Process approved/pending transactions one at a time through
   `transaction_create`.
2. Mark each successful staged transaction `committed`.
3. Mark its source email `recorded` only after the MCP call succeeds.
4. Keep failed transactions in a retryable state with `commit_error`.
5. Continue processing after an individual failure.
6. Mark the batch `committed` only when no eligible transactions remain; leave
   it `approved` when failures remain.

No Money Manager mutation occurs during import or review.

## Verification

- Test Gmail date range construction and pagination.
- Test staging idempotency and monthly grouping.
- Test category grounding and uncertainty/duplicate flags.
- Test editing and validation of all supported fields.
- Test partial commit, retry behavior, and email marking after MCP success.
- Test Telegram command formatting and full-suite regression behavior.
- Test help, invalid syntax, commit previews, and explicit confirmation behavior.
