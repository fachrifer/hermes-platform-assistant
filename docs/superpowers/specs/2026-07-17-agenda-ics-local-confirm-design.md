# Agenda Confirm → Local + ICS Design

Date: 2026-07-17  
Status: Approved (pending user review of written spec)

## Goal

All agenda creation goes through an explicit Telegram confirm flow: LLM
recommendation, optional edit, then user Acc. Acc persists the item in local
SQLite (briefing + Hermes reminders) and sends an `.ics` file for the user to
open on iOS / macOS / Android Calendar, which then syncs into Google Calendar on
the device. Hermes does not call Gmail, Google Calendar, or Outlook APIs.

## Decisions

| Topic | Choice |
|---|---|
| After Acc | `.ics` + SQLite local (brief / reminder) |
| Edit before Acc | Button **Edit** → reply correction text → refresh card |
| Input coverage | All: image/PDF, natural language, `/agenda tambah` |
| Approach | Extend existing `pending_agenda` Catat/Lewati queue |
| Remote calendars | Remove Gmail, Google Calendar OAuth, and Outlook |
| Edit saved items | `/agenda edit <id>` or NL → same Acc/Edit/Lewati card |
| Device calendar | User opens `.ics` and taps Add (user remains the acceptor) |

## Main Flow

```
Input (image / PDF / NL text / /agenda tambah)
        ↓
LLM extract + short recommendation
        ↓
Telegram draft card: Acc | Edit | Lewati
        ↓
Edit → reply corrections → card updated → choose again
Acc  → SQLite save/update + reply .ics document
Lewati → drop item, show next in queue
```

## Telegram UX

### Draft card (one item per message)

Shown fields:

- Title, date, start–end time, location, notes
- Short LLM recommendation line (why times/fields were chosen, uncertainty)
- `⚠️ Perlu review` when `needs_review`
- Progress `Agenda N/M` when queued from a multi-item extraction

Buttons:

| Button | Action |
|---|---|
| ✅ Acc | Persist local row + send `.ics` for this item |
| ✏️ Edit | Enter edit mode; next user reply applies field corrections |
| ⏭️ Lewati | Discard item; show next |

Replace the previous **Catat** label with **Acc** for clarity (same confirm gate).

### Edit reply format

Free-form corrections, one or more fields, for example:

- `judul: Rapat Tim`
- `tanggal: 2026-07-20`
- `jam: 15:00-16:00`
- `lokasi: Zoom`
- `catatan: bawa deck`
- `reminder: 30m`

Invalid parse → short error, remain in edit mode, draft unchanged.  
Valid parse → update in-memory draft, re-render card with Acc/Edit/Lewati.

### Queue behavior

- FIFO `pending_agenda` token state (same pattern as today’s attachment flow)
- Acc/Lewati pops current item and advances
- End summary: `dicatat X, dilewati Y`
- In-memory only: container restart drops unfinished drafts; Acc’d rows remain in SQLite

### Text / NL / `/agenda tambah`

Must not save immediately. Normalize into one draft item and enqueue the same
confirm UI.

## Acc Behavior

1. Write or update SQLite agenda row (see schema below); obtain `id` first so
   `.ics` `UID` can be stable.
2. If `date` + `start_time` are present: build a single-event `.ics` (`VEVENT`)
   and `reply_document` on Telegram.
3. If date/time is missing: save local only (still Acc’d for briefing); no `.ics`;
   tell the user to Edit if they want a calendar file.
4. Caption when `.ics` is sent: open the file → Add in phone/Mac Calendar; use
   the Google calendar account as the target so device sync pushes to Google.
5. Schedule / refresh Hermes local reminder from stored reminder minutes
   (Telegram notification), independent of Google.

If `.ics` generation fails: still keep the local row and warn the user.

Draft extraction should include an optional short `recommendation` string for
the card (plus existing title/date/times/location/notes/reminders/needs_review).

## ICS Contents

- `UID` unique per Hermes agenda id (stable across re-exports when editing)
- `SUMMARY` (title), `DTSTART` / `DTEND` (Hermes configured timezone)
- `LOCATION`, `DESCRIPTION` (notes)
- Optional `VALARM` when reminder minutes are set
- One Acc → one `.ics` file (multi-item Acc happens one card at a time)

Hermes never updates or deletes events already added on the user’s Google
Calendar. Re-Acc after edit sends a new `.ics`; the user adds/replaces on device.

## Edit Previously Saved Agenda

Entry points:

- `/agenda` lists local items with ids
- `/agenda edit <id>`
- Natural language such as “ubah agenda 3 …”

Behavior:

- Load row into a draft card (queue length 1) with Acc / Edit / Lewati
- **Edit** = same reply-correction flow as create
- **Acc** = update SQLite + send new `.ics` + refresh local reminder schedule
- **Lewati** = cancel edit session; stored row unchanged

Delete remains separate (`/agenda` hapus / “hapus agenda N”) and does not use
the Acc card.

## Local Schema

Extend the `agenda` table beyond `title` / `when_at` / `notes` so briefing,
reminders, edit, and ICS round-trip cleanly. Required fields after migration:

- `title` (required)
- `date` (YYYY-MM-DD, nullable)
- `start_time` / `end_time` (HH:MM, nullable)
- `location` (nullable)
- `notes` (nullable)
- `reminder_minutes` (nullable integer)
- `when_at` kept or derived for briefing sort compatibility
- `created_at` / `updated_at`

Exact migration strategy is an implementation detail; existing rows must remain
readable (backfill from `when_at` / `notes` where possible).

## Reminder Policy

- Draft may include reminder minutes from LLM, parse, or Edit (`reminder: 30m`)
- After Acc, Hermes schedules a Telegram reminder from local data
- Editing a saved agenda updates that schedule
- Device calendar alarms (from `.ics` VALARM) are separate and owned by the
  phone/Mac Calendar app

## Remove Remote Calendar Integrations

Remove from runtime, config, scripts, docs, and tests as applicable:

- Gmail connector / OAuth / related env
- Google Calendar connector / OAuth / `scripts/google_login.py` / `GOOGLE_*`
- Outlook / Microsoft Graph / `scripts/ms_login.py` / `MS_*`
- Planner actions: `calendar_query`, `calendar_update_reminders`, GCal write in
  `save_agenda_item`
- Briefing sections that read Google or Outlook calendars

Agenda and briefing then use **local SQLite only**.

Keep: Telegram, Gemini, Tavily, finance spreadsheet flows, manual agenda SQLite,
persona/memory.

## Error Handling

| Case | Behavior |
|---|---|
| Extraction empty / failed | Clear message; no Acc card |
| Missing title on Acc | Reject Acc; ask Edit |
| Acc without date/time | Save local only; no `.ics`; explain |
| Unparseable edit reply | Ask again; draft unchanged |
| ICS build failure | Save local + warn |
| Unknown agenda id on edit | Error; no card |
| Process restart mid-draft | Pending drafts lost |

## Non-Goals

- Server-side Google Calendar create/update/delete
- Tasker or fully automatic device add without opening `.ics`
- Persistent pending-draft DB (unless added later)
- CalDAV / Exchange connectors

## Supersedes

This design supersedes the Google Calendar write path in
`2026-07-16-agenda-file-calendar-design.md` and the Google Calendar
query/reminder remote flows in
`2026-07-16-google-calendar-query-reminder-design.md` for Hermes runtime
behavior. Those docs remain historical unless rewritten later.

## Verification (design-level)

- Create via image, NL, and `/agenda tambah` all show Acc/Edit/Lewati before save
- Edit reply updates the card without saving
- Acc writes SQLite and attaches `.ics`
- `/agenda edit <id>` can change a saved row and re-export `.ics`
- Briefing lists only local Acc’d items
- No Google/Outlook API usage remains on the agenda path
