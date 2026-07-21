# Gmail Finance Import Restore Design

## Goal

Restore multi-account Gmail for **finance scan only** (Pribadi + Kerja), keep
manual paste/upload as a dual `/import` mode, and leave Google Calendar
read/write on the personal (`Pribadi`) account unchanged.

SearXNG / Whoogle and inbox chat/search are out of scope.

## Decisions

| Topic | Choice |
|---|---|
| Gmail purpose | Finance scan only (not inbox briefing) |
| Accounts | Pribadi + Kerja Gmail; Calendar Pribadi only |
| Intake UX | Dual mode: Gmail scan and Paste/upload |
| Bare `/import` | Ask mode with inline buttons (Gmail / Paste) |
| Calendar | Keep current read + write on Pribadi |
| Search engines | Not in this change |

## Current State

- `GmailConnector` was removed; `/import` opens a paste/upload session only.
- `FinanceImportService.import_period` is a stub that returns `manual_intake: True`.
- Shared Google OAuth (`connectors/google_auth.py`) is Calendar-only scopes.
- `GoogleCalendarConnector` already reads/writes the configured write account
  (`GOOGLE_CALENDAR_WRITE_ACCOUNT=Pribadi`).
- Multi-account token paths/labels already exist in settings and `.env`.
- Staging, `/batch`, `/edit`, and `/export` already work for paste intake.

## Architecture

```text
Telegram /import
    │
    ├─ no mode → inline buttons: Gmail | Paste
    │
    ├─ mode=gmail → GmailConnector.scan_finance_range(Pribadi+Kerja)
    │                 → FinanceImportService.stage_from_sources
    │                 → /batch → /export
    │
    └─ mode=paste → existing paste/upload session
                      → stage_paste / stage_upload
                      → /batch → /export

Google Calendar (unchanged) → Pribadi only (query / create / reminders)
```

### Components

| Unit | Responsibility |
|---|---|
| `connectors/google_auth.py` | Shared OAuth load/refresh; scopes include Gmail + Calendar |
| `connectors/gmail.py` | Multi-account Gmail readonly; finance range scan; health |
| `connectors/gcal.py` | Unchanged Pribadi Calendar policy |
| `core/finance_import.py` | Parse dual-mode `/import`; restore Gmail `import_period` |
| `core/telegram_bot.py` | Mode picker callbacks; route gmail vs paste |
| `core/agent.py` | Register `GmailConnector`; pass into finance import |
| `scripts/google_login.py` | Issue tokens with updated scopes |
| `/status` / `/brief` | Show Gmail health (aggregate) + Calendar |

## `/import` UX

| Input | Behavior |
|---|---|
| `/import` | Default period = current `YYYY-MM`; send mode picker buttons |
| `/import YYYY-MM` | Same period; send mode picker buttons |
| `/import YYYY-MM force` | Mode picker; force applies after Gmail is chosen |
| `/import gmail [YYYY-MM] [force]` | Scan both Gmail accounts into staging |
| `/import paste [YYYY-MM]` | Open paste/upload session (current flow) |
| `/import done` / `/import cancel` | Close/cancel active paste session only |

Mode picker message (Indonesian, Nyx-neutral):

```text
📥 Import {period}
Pilih sumber:

[ Gmail ]  [ Paste ]
```

Callback data:

- `import_mode:gmail:{period}:{force}` where `force` is `0` or `1`
- `import_mode:paste:{period}:0`

After Gmail scan completes, reply with staged/skipped/extract_errors counts and
point to `/batch {period}`. Do not open a paste session.

If no Gmail account is usable, fail with an actionable re-login hint and suggest
`/import paste {period}`.

## Accounts and OAuth

### Scopes (`connectors/google_auth.py`)

```text
https://www.googleapis.com/auth/gmail.readonly
https://www.googleapis.com/auth/calendar.readonly
```

When `GOOGLE_CALENDAR_WRITE=1`, login also requests:

```text
https://www.googleapis.com/auth/calendar.events
```

### Account policy

- **Pribadi:** Gmail finance scan + Calendar read/write.
- **Kerja:** Gmail finance scan only. Calendar APIs are not used for Kerja even
  if the token happens to include Calendar scopes.
- Config remains:

```env
GOOGLE_TOKEN_PATHS=/app/credentials/google_token.json,/app/credentials/google_token_2.json
GOOGLE_ACCOUNT_LABELS=Pribadi,Kerja
GOOGLE_CALENDAR_WRITE_ACCOUNT=Pribadi
GOOGLE_CALENDAR_WRITE=1
```

### Re-login

Changing scopes invalidates existing tokens for new APIs. Operator must run
`python -m scripts.google_login` once per account (override `GOOGLE_TOKEN_PATH`
per run). Prefer OAuth consent screen **In production** so refresh tokens do not
expire every 7 days (ops guidance; not a code requirement of this design).

## Gmail connector behavior

`GmailConnector` implements the shared `Connector` interface.

### Health

- `is_available()`: at least one configured token path exists.
- `health_check()`: true if at least one account can call Gmail
  `users().getProfile()` (or equivalent lightweight call).
- Per-account failures are logged with the account label; never log token
  contents.

### Finance range scan

`scan_finance_range(start: date, end: date, max_results: int = 100) -> list[dict]`

1. Load all usable Google accounts.
2. For each account, build a Gmail search query using:
   - Inclusive date range via exclusive `after:` / `before:` operators
     (same rule as the historical import design: end date + 1 day).
   - `settings.finance_senders_list` and `settings.finance_keywords_list`.
3. Paginate `users().messages().list` until exhausted or `max_results` reached
   **per account**.
4. Fetch `format="full"`, extract plaintext body.
5. Merge results. Each message dict includes:

```python
{
  "id": f"{label}:{gmail_message_id}",
  "account": label,
  "from": ...,
  "subject": ...,
  "date": ...,
  "body": ...,
}
```

Account-qualified IDs are mandatory for dedupe across two inboxes.

One account failing must not abort the other.

## Finance import integration

Restore `FinanceImportService.import_period(period, *, force=False)`:

1. Optional `force` clears skipped emails with summary `"bukan transaksi"`
   (existing behavior).
2. Resolve inclusive date range via `parse_import_period`.
3. Call `self.gmail.scan_finance_range(...)`.
4. Pass sources into existing `stage_from_sources`.
5. Return staged/skipped/extract_errors/batch metadata (no `manual_intake`).

Paste/upload paths remain unchanged.

Constructor accepts an optional `gmail` connector again (required for Gmail
mode; paste mode still works if `gmail` is `None`, but Gmail mode must refuse
clearly).

## Calendar

No behavioral change:

- Query, create, and reminder updates use Pribadi only.
- Kerja remains unused for Calendar.
- Existing `GOOGLE_CALENDAR_WRITE` gate remains.

## Agent / briefing / status

- Register `GmailConnector` in `HermesAgent.connectors` so `/status` and
  `/brief` show Gmail health.
- Do **not** add Gmail inbox contents to `/brief`.
- Natural-language `scan_email` intent continues to direct users to `/import`
  (mode picker), not a silent auto-scan.

## Error handling

| Case | Response |
|---|---|
| Missing token file for one account | Log + skip; continue with other account |
| No usable Gmail accounts | Error + re-login hint + suggest paste |
| Expired/revoked refresh token | Account skipped; message names the label |
| Gemini extract failure | Count as `extract_errors`; do not mark skipped |
| Calendar Pribadi down | Existing Calendar errors; Gmail scan unaffected |

## Testing

- Parse `/import` modes: bare → choose; `gmail` / `paste` / `done` / `cancel` /
  period / force.
- Gmail range query inclusive date bounds + sender/keyword filters.
- Multi-account merge, skip failed account, qualified email IDs.
- `import_period` stages from mocked Gmail sources; paste path still works.
- Telegram mode-picker callback routes without starting the wrong mode.
- Calendar Pribadi-only behavior remains covered by existing tests.

## Docs / config

Update `.env.example` and `README.md`:

- Gmail OAuth is restored for finance scan (Pribadi + Kerja).
- Dual `/import` modes and mode picker.
- Re-login steps after scope change.
- Clarify Calendar remains Pribadi-only.

## Out of scope

- SearXNG / Whoogle
- Gmail inbox search / unread briefing
- Kerja Calendar
- Removing paste/upload
- Publishing OAuth consent screen (operator checklist only)
- Outlook changes

## Supersedes

This design **reopens** Gmail finance connectivity that the
`2026-07-17-agenda-ics-local-confirm-design` draft proposed to remove. Calendar
API on Pribadi remains; the ICS-local agenda plan is not implemented by this
change and is not revived here.
