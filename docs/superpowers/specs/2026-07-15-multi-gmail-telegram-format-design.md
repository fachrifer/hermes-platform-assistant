# Multi-Account Gmail and Telegram Formatting

## Goal

Support two Gmail accounts in one Hermes instance by combining their email and
Google Calendar data, while making Telegram output render consistently without
literal Markdown markers such as `**`.

## Current Behavior

- `GmailConnector` reads one token from `GOOGLE_TOKEN_PATH`.
- `GoogleCalendarConnector` reuses that same token.
- `/brief` and transaction confirmations use legacy Telegram Markdown.
- LLM responses are sent as plain text, so Markdown emitted by the LLM can
  appear literally, including `**bold**`.

## Configuration

Add a comma-separated list of token paths and optional matching labels:

```env
GOOGLE_CLIENT_SECRETS=/app/credentials/fferdian_google_client_secret.json
GOOGLE_TOKEN_PATHS=/app/credentials/google_token_1.json,/app/credentials/google_token_2.json
GOOGLE_ACCOUNT_LABELS=Pribadi,Kerja
```

`GOOGLE_TOKEN_PATHS` is the source of truth for multi-account operation. Empty
entries are ignored. Labels are optional; missing labels fall back to an
account number. The existing singular token setting remains accepted as a
fallback when the list setting is absent, so existing installations continue to
work while they migrate.

The OAuth login script accepts the configured token path override and is run
once per account. Each OAuth token is independent even when both accounts use
the same OAuth client-secret JSON.

## Gmail and Calendar Data Flow

1. Load each configured token independently.
2. Refresh valid refreshable credentials and persist the refreshed token to the
   same path.
3. Skip unavailable or invalid accounts without preventing other accounts from
   working.
4. Run Gmail health checks and queries for every usable account.
5. Merge results and attach the configured account label to each email or event.
6. Keep the existing connector interfaces so the agent, briefing, and scan
   workflows continue to call one Gmail and one Calendar connector.

Email IDs used for deduplication and confirmation must include the account
identity, because two accounts can produce the same Gmail message ID.

Health reporting will expose aggregate connector health: the connector is
healthy when at least one account is usable. Account-specific failures should be
logged with the account label without logging token contents.

## Telegram Formatting

Introduce one safe formatter for generated Telegram text and use it for briefing,
startup, LLM replies, and transaction confirmation messages. The formatter will
normalize the small Markdown subset currently used by Hermes, including:

- `**bold**` and `*bold*` rendered as bold;
- `_italic_` rendered as italic;
- escaped Telegram-sensitive characters;
- plain text preserved when no formatting markers are present.

The implementation will use one consistent Telegram parse mode and fall back to
plain text if formatted delivery is rejected. User-provided email subjects,
senders, transaction descriptions, and LLM output must be escaped before being
inserted into formatted messages.

## Error Handling

- Missing token for one account is reported in logs and does not disable the
  other account.
- No usable account keeps Gmail and Calendar unavailable as today.
- OAuth refresh failures are isolated per account.
- Telegram formatting failures must not prevent the response from being sent;
  retry the same content without parse mode.

## Testing

Add focused tests for:

- parsing token paths and account labels;
- loading, refreshing, skipping, and merging multiple Google accounts;
- account-qualified email IDs and labels;
- combined Gmail and Calendar results with one account unavailable;
- conversion of `**bold**`, existing single-star formatting, italic text, and
  special characters into safe Telegram output;
- fallback to plain Telegram text after a formatting rejection.

## Scope Limits

- No account-selection Telegram command is added; both accounts are always
  combined.
- No public Google OAuth verification workflow is implemented.
- No unrelated Telegram UI redesign or connector refactoring is included.
