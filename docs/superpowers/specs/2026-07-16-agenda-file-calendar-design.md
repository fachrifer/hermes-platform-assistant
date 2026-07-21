# Agenda File Extraction and Google Calendar Write Design

## Goal

Allow Nyx to read agenda details from Telegram images and PDFs, ask for
confirmation one item at a time, and save confirmed timed events to the personal
Google Calendar while retaining SQLite as a fallback.

## Account Policy

- Google account `Pribadi` is the only account allowed to write Calendar events.
- Google account `Kerja` remains Gmail read-only and is never used for Calendar
  writes.
- `GOOGLE_CALENDAR_WRITE_ACCOUNT=Pribadi` makes the write target explicit.
- The personal token must be re-authorized with Calendar write scope; the work
  token remains read-only.

## File Input

Telegram accepts photos and PDF/image documents. Nyx downloads the attachment to
temporary storage, sends the bytes and MIME type to Gemini multimodal extraction,
and removes the temporary file after processing.

The extraction schema contains:

- `title`
- `date`
- `start_time`
- `end_time`
- `location`
- `notes`
- `needs_review`

All extracted agenda items are shown one at a time. Each item has **Catat** and
**Lewati** actions.

## Agenda Storage Policy

- A confirmed item with a valid date and start time is created in Google Calendar
  `Pribadi` as the primary record.
- A confirmed item without a usable date/time is stored in the local SQLite
  agenda table.
- If Google Calendar write fails, the item is stored locally with a fallback
  marker and the user is told that it was not created remotely.
- Local-only agenda entries remain available through `/agenda` and briefing.
- After any local save, the system reads the saved row back before reporting
  success, preventing false success messages.

## Existing `/agenda` Behavior

- `/agenda` lists local agenda records from the same SQLite database used by
  `/agenda tambah`.
- `/agenda tambah <judul>` continues to support local entries and can parse an
  optional date/time.
- The response states whether the item was saved to Google Calendar or SQLite
  fallback.
- Briefing must avoid showing the same event twice when a Google-created event
  has a local fallback record.

## Google Calendar Connector

Add write capability to the existing connector:

- Read all configured accounts for agenda display as before.
- Select only the configured write account for `events.insert`.
- Use the account's primary calendar by default.
- Convert local date/time and timezone settings into Calendar event payloads.
- Return the created event ID and HTML link when available.
- Handle missing write scope, disabled Calendar API, expired token, and network
  errors with actionable messages.

## OAuth Scope Migration

- Keep the normal read scopes for Gmail and Calendar reads.
- Add a write-enabled login mode for the personal account using
  `calendar.events`.
- Re-login only the personal token after deleting its local cached token file.
- Do not require or request Calendar write permission for the work account.

## Verification

- Test image/PDF MIME routing and extraction normalization without external API
  calls.
- Test one-by-one confirmation and skip behavior.
- Test personal-account selection and rejection of work-account writes.
- Test Google event payload conversion and fallback SQLite behavior.
- Test `/agenda` read-after-write consistency.
- Test OAuth scope configuration and actionable error messages.
- Run the full test suite and verify the container starts healthy.
