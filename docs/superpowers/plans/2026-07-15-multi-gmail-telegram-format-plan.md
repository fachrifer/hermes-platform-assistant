# Multi-Account Gmail and Telegram Formatting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automatic aggregation for two Google accounts and make every Telegram response render safely without literal Markdown markers.

**Architecture:** Keep one Gmail connector and one Calendar connector, but make each internally iterate over independently configured OAuth token files. Add a small Telegram formatting/delivery module that converts the existing Markdown subset to safe HTML and provides a plain-text fallback.

**Tech Stack:** Python 3.11, pytest, `google-auth`, `google-api-python-client`, `python-telegram-bot`, Docker Compose.

## Global Constraints

- Both Google accounts are always combined; no account-selection command is added.
- Each Google account has an independent OAuth token file.
- A missing or invalid account must not disable another usable account.
- Email deduplication IDs must include account identity.
- Telegram output must not expose literal `**` formatting markers.
- No token contents or secrets may be logged.
- Preserve the singular `GOOGLE_TOKEN_PATH` as a fallback for existing installations.

---

### Task 1: Add Multi-Account Google Settings

**Files:**
- Modify: `config/settings.py`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/test_google_settings.py`

**Interfaces:**
- Produces `Settings.google_token_paths: list[str]` and `Settings.google_account_labels: list[str]`.
- `google_token_paths` reads `GOOGLE_TOKEN_PATHS` when set, otherwise returns the configured singular `GOOGLE_TOKEN_PATH` as a one-item list.
- Empty comma-separated entries are ignored.
- Labels are trimmed and missing labels use `Akun 1`, `Akun 2`, and so on.

- [ ] **Step 1: Write the failing tests**

```python
from config.settings import Settings


def test_token_paths_and_labels_are_parsed(monkeypatch):
    monkeypatch.setenv("GOOGLE_TOKEN_PATHS", " /one.json, ,/two.json ")
    monkeypatch.setenv("GOOGLE_ACCOUNT_LABELS", "Pribadi,Kerja")

    current = Settings()

    assert current.google_token_paths == ["/one.json", "/two.json"]
    assert current.google_account_labels == ["Pribadi", "Kerja"]


def test_singular_token_path_is_backward_compatible(monkeypatch):
    monkeypatch.delenv("GOOGLE_TOKEN_PATHS", raising=False)
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", "/legacy.json")
    monkeypatch.delenv("GOOGLE_ACCOUNT_LABELS", raising=False)

    current = Settings()

    assert current.google_token_paths == ["/legacy.json"]
    assert current.google_account_labels == ["Akun 1"]


def test_missing_labels_get_account_numbers(monkeypatch):
    monkeypatch.setenv("GOOGLE_TOKEN_PATHS", "/one.json,/two.json")
    monkeypatch.setenv("GOOGLE_ACCOUNT_LABELS", "Pribadi")

    current = Settings()

    assert current.google_account_labels == ["Pribadi", "Akun 2"]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/test_google_settings.py -q`

Expected: FAIL because `Settings` does not expose the multi-account properties.

- [ ] **Step 3: Implement the settings properties**

Add `google_token_paths` and `google_account_labels` properties. Parse comma-separated values with the existing `_get` helper, use `GOOGLE_TOKEN_PATH` as the fallback, and generate `Akun N` labels when no configured label exists.

- [ ] **Step 4: Update configuration documentation**

Document the following in `.env.example` and the Google login section of `README.md`:

```env
GOOGLE_TOKEN_PATHS=/app/credentials/google_token_1.json,/app/credentials/google_token_2.json
GOOGLE_ACCOUNT_LABELS=Pribadi,Kerja
```

Explain that `python3 -m scripts.google_login` must be run once per account with a different `GOOGLE_TOKEN_PATH` override.

- [ ] **Step 5: Run the tests and verify they pass**

Run: `pytest tests/test_google_settings.py -q`

Expected: all settings tests pass.

---

### Task 2: Aggregate Gmail and Calendar Accounts

**Files:**
- Modify: `connectors/gmail.py`
- Modify: `connectors/gcal.py`
- Modify: `core/agent.py`
- Modify: `config/settings.py`
- Test: `tests/test_google_connectors.py`

**Interfaces:**
- Add `load_all_credentials() -> list[GoogleAccount]` in `connectors/gmail.py`.
- `GoogleAccount` contains `label: str`, `token_path: Path`, and valid `credentials`.
- Existing `GmailConnector.summary`, `scan_finance`, and Calendar `today_events` continue returning lists and now merge usable accounts.
- Returned email dictionaries contain `account` and use `f"{label}:{message_id}"` as `id`.
- Returned calendar dictionaries contain `account`.

- [ ] **Step 1: Write failing unit tests with mocked Google credentials and services**

Cover these cases:

```python
def test_missing_second_token_does_not_disable_first_account():
    monkeypatch.setattr(settings, "google_token_paths", ["/one.json", "/two.json"])
    monkeypatch.setattr(settings, "google_account_labels", ["Pribadi", "Kerja"])
    monkeypatch.setattr(gmail_module.Path, "exists", lambda path: str(path) == "/one.json")
    monkeypatch.setattr(gmail_module, "_credentials_from_file", lambda path, scopes: fake_credentials)
    monkeypatch.setattr(gmail_module, "_build_service", fake_gmail_service)

    assert asyncio.run(GmailConnector().health_check()) is True
    assert asyncio.run(GmailConnector().summary()) == expected_first_account_messages


def test_email_ids_are_account_qualified():
    configure_two_fake_accounts(monkeypatch)
    messages = asyncio.run(GmailConnector().scan_finance())

    assert {message["id"] for message in messages} == {"Pribadi:same-id", "Kerja:same-id"}


def test_calendar_events_include_account_label():
    configure_two_fake_accounts(monkeypatch)
    events = asyncio.run(GoogleCalendarConnector().today_events())

    assert {event["account"] for event in events} == {"Pribadi", "Kerja"}
```

Tests must mock file existence and Google API construction; they must never call Google or require real credentials.

- [ ] **Step 2: Run the connector tests and verify they fail**

Run: `pytest tests/test_google_connectors.py -q`

Expected: FAIL because the connectors only load one token and do not attach account identity.

- [ ] **Step 3: Implement account loading and isolated refresh**

Refactor the current single-token loader into a per-path loader. Iterate over `settings.google_token_paths`, skip absent or invalid files, refresh valid refreshable credentials in place, and log only the account label and failure reason. Keep `_load_credentials()` as a compatibility helper only if existing imports require it, and make it return the first usable account credentials.

- [ ] **Step 4: Implement merged Gmail queries**

For each usable account, build a Gmail service, run the existing query, attach `account`, and qualify each message ID. Catch failures around each account so one service failure does not abort the merged result. Apply this to `summary` and `scan_finance`.

- [ ] **Step 5: Implement merged Calendar queries**

Reuse the account loader from `connectors.gmail`, build one Calendar service per usable account, query each primary calendar, and attach `account` to every returned event. Make health true when at least one account can be checked successfully.

- [ ] **Step 6: Verify database handling for qualified IDs**

Read the existing email-handling schema and add a regression test that calls `mark_email("Pribadi:same-id", ...)` and `is_email_handled("Pribadi:same-id")`. If the schema or typing rejects the value, change only that field boundary to accept the qualified string.

- [ ] **Step 7: Run all Google tests**

Run: `pytest tests/test_google_settings.py tests/test_google_connectors.py -q`

Expected: all tests pass without network access.

---

### Task 3: Add Safe Telegram Formatting

**Files:**
- Create: `core/telegram_format.py`
- Modify: `core/telegram_bot.py`
- Modify: `core/briefing.py`
- Test: `tests/test_telegram_format.py`

**Interfaces:**
- `format_telegram(text: str) -> str` converts the supported Markdown subset to escaped Telegram HTML.
- `send_formatted(message, text, **kwargs) -> TelegramMessage` sends HTML and retries plain text if Telegram rejects parsing.

- [ ] **Step 1: Write failing formatter tests**

```python
from core.telegram_format import format_telegram


def test_double_star_bold_becomes_html_bold():
    assert format_telegram("**Penting**") == "<b>Penting</b>"


def test_single_star_and_underscore_are_converted():
    assert format_telegram("*Judul* _catatan_") == "<b>Judul</b> <i>catatan</i>"


def test_special_characters_are_escaped():
    assert format_telegram("A & B < C") == "A &amp; B &lt; C"
```

- [ ] **Step 2: Run formatter tests and verify they fail**

Run: `pytest tests/test_telegram_format.py -q`

Expected: FAIL because the formatter module does not exist.

- [ ] **Step 3: Implement the minimal safe formatter**

Escape text with `html.escape` first, then convert only balanced `**...**`, `*...*`, and `_..._` spans. Do not interpret arbitrary HTML from LLM output or user-provided content. Preserve newlines and bullet characters.

- [ ] **Step 4: Add formatted-send fallback**

Send with `ParseMode.HTML`; if the Telegram API raises a formatting-related exception, resend the original text without `parse_mode`. Do not swallow unrelated network errors silently.

- [ ] **Step 5: Route every relevant response through the formatter**

Use the helper for `/brief`, startup brief, natural-language LLM responses, transaction confirmation cards, and callback result messages. Keep keyboard behavior unchanged.

- [ ] **Step 6: Add account labels to user-visible merged data**

Update briefing email lines and transaction confirmation text to include the `account` field when present. Escape subject, sender, description, and account label through the formatter.

- [ ] **Step 7: Run formatter tests**

Run: `pytest tests/test_telegram_format.py -q`

Expected: all formatter tests pass.

---

### Task 4: Documentation and End-to-End Verification

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Test: `tests/test_integration_boundaries.py`

- [ ] **Step 1: Add configuration and login verification tests**

Test that two configured token paths and labels are exposed to the runtime, while the login script still honors a `GOOGLE_TOKEN_PATH` environment override and writes to the requested path.

- [ ] **Step 2: Document the two-account setup**

Show host-path commands for OAuth login, one command per Gmail account, and the container-path values used by Docker Compose. State that both accounts are merged automatically and that each account must be added as a Google OAuth test user during testing.

- [ ] **Step 3: Run the complete test suite**

Run: `pytest -q`

Expected: all tests pass.

- [ ] **Step 4: Run a container configuration check**

Run: `docker compose config`

Expected: configuration succeeds and the credentials volume maps to `/app/credentials`.

- [ ] **Step 5: Run a no-secret runtime smoke test**

Run a container command that checks only the existence of configured token paths and labels, never printing token contents.

Expected: settings load successfully and the process remains healthy when one or both token files are absent.

## Self-Review

- Multi-account settings, fallback compatibility, isolated account failures, merged Gmail data, merged Calendar data, account-qualified IDs, Telegram conversion, HTML escaping, and plain-text fallback each have an explicit task.
- No step requires real Google credentials or a network call in unit tests.
- All names used by later tasks are defined in earlier task interfaces.
- No public OAuth verification or auto-trading behavior is included.
