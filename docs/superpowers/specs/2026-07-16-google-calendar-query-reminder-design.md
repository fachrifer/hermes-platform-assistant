# Google Calendar Query and Reminder Design

## Goal

Allow Nyx to answer natural-language Google Calendar queries from the personal
account and create Calendar events with reminders exactly as requested by the
user.

## Calendar Account Policy

- Read and write Calendar only through the `Pribadi` account.
- The `Kerja` account remains Gmail read-only.
- Natural-language Calendar queries must use the same personal account as event
  writes.
- Missing read scope, disabled API, expired token, and network errors must be
  reported as actionable connection errors.

## Calendar Query

Add a `calendar_query` intent for requests such as:

- `cek agenda saya di Google Calendar`
- `ada agenda besok?`
- `jadwal saya minggu ini apa?`

The query flow determines a date range, reads the personal Calendar using
`calendarList`/`events.list`, and formats the events with date, time, title,
location, and source. It must not claim that access is unavailable when the
configured connector is merely empty or has a recoverable error.

## Reminder Policy

- Events have **no reminder by default**.
- A reminder request without a method uses Google Calendar `popup`.
- `email` is used only when the user explicitly requests email notification.
- Multiple requested reminders are preserved.
- Supported examples:

```text
ingatkan 30 menit sebelumnya
ingatkan 1 jam sebelumnya lewat email
ingatkan 1 hari sebelumnya dengan popup dan 30 menit sebelumnya lewat email
```

The event payload uses:

```json
{
  "reminders": {
    "useDefault": false,
    "overrides": [
      {"method": "popup", "minutes": 30},
      {"method": "email", "minutes": 1440}
    ]
  }
}
```

When no reminder is requested, omit `reminders` or use an empty override list;
do not silently add Google defaults.

## Input Sources

Reminder parsing applies consistently to:

- `/agenda tambah` with explicit date/time and reminder text.
- Natural-language agenda requests.
- Confirmed agenda items extracted from image/PDF files when the extracted
  notes or request includes reminder instructions.

The confirmation preview shows the destination account and reminder list before
calling `events.insert`.

## Existing Event Reminder Updates

Add a `calendar_update_reminders` intent for requests such as:

```text
ubah reminder Kereta Parahyangan 134B menjadi 3 jam sebelumnya
sesuaikan semua agenda perjalanan Kereta Parahyangan 139B ke 3 jam sebelumnya
```

The update flow:

1. Query matching events from personal Google Calendar, including event IDs.
2. Show matching events one at a time with the proposed reminder change.
3. Offer **Konfirmasi** and **Lewati** for each event.
4. Call `events.update` only after **Konfirmasi**.
5. Report the actual Google Calendar result; never claim an update before the
   API call succeeds.

If no event matches, report that no change was made. If several events match,
each remains independently confirmable.

## Verification

- Test `calendar_query` intent routing and date-range construction.
- Test personal-account-only reads and writes.
- Test reminder parsing for popup, explicit email, multiple reminders, and no
  reminder.
- Test Calendar payload omission when no reminder is requested.
- Test query formatting and actionable OAuth/API errors.
- Run the full suite and verify the container starts healthy.
