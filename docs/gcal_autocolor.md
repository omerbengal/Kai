# Calendar auto-color by organizer

`scripts/gcal_autocolor.gs` is a Google Apps Script that runs in Omer's own Google account.
Every event in his primary calendar organized by `<REDACTED_SECRET>` gets colorId 5 (Banana / yellow).

- Trigger: installable calendar trigger `onEventUpdated` on his calendar. The trigger does not say which
  event changed, so the handler reads the delta with an incremental sync token (UserProperties).
  A 410 / expired token falls back to a full sync of upcoming events and recurring series.
- Loop guard: patch only when the organizer matches (case-insensitive) and colorId differs; skip cancelled.
- The color is per-calendar: it changes only Omer's copy, not the organizer's or other guests'.
- Pending invitations (needsAction) are in the calendar too (setting `hideInvitations` = false),
  so they are colored as well. An RSVP change fires the trigger, and the color is re-checked.

Verified 15/10/2026 via the Calendar API (credentials/google_token.json):
a pending invite colored and stayed needsAction; a colorId + an RSVP write on an accepted event kept
colorId 5; after the one-time run, all 732 upcoming instances (731 pending) read back as colorId 5.

Setup: script.google.com, New project, paste the file, Services + "Google Calendar API", run `install`,
approve. Off: run `uninstall`.
