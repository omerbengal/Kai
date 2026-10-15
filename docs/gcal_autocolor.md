# Calendar auto-color by organizer

`scripts/gcal_autocolor.gs` is a Google Apps Script that runs in Omer's own Google account.
Every event in his primary calendar whose organizer is a key of `RULES` (organizer email, lowercase -> colorId)
gets that color. One project, one file, one trigger for all rules:

| Organizer | colorId | UI name |
|-----------|---------|---------|
| <REDACTED_SECRET> | 5 | Banana (yellow) |
| <email> (<partner>) | 6 | Tangerine (red-orange) |

Event colors are Google's fixed 11-color palette; a custom hex per event is not possible. Omer asked for #ff4000:
the Calendar UI (modern palette) renders Tangerine as ~#F4511E and Tomato as ~#D50000, so Tangerine is the closest.
Note: `colors.get` returns the legacy hexes (6 = #ffb878, 11 = #dc2127), not what the UI shows.

- Trigger: installable calendar trigger `onEventUpdated` on his calendar. The trigger does not say which
  event changed, so the handler reads the delta with an incremental sync token (UserProperties).
  A 410 / expired token falls back to a full sync of upcoming events and recurring series.
- Loop guard: patch only when the organizer has a rule (case-insensitive) and colorId differs; skip cancelled.
- The color is per-calendar: it changes only Omer's copy, not the organizer's or other guests'.
- Pending invitations (needsAction) are in the calendar too (setting `hideInvitations` = false),
  so they are colored as well. An RSVP change fires the trigger, and the color is re-checked.

Verified 15/10/2026 via the Calendar API (credentials/google_token.json):
a pending invite colored and stayed needsAction; a colorId + an RSVP write on an accepted event kept
colorId 5; after the one-time run, all 732 upcoming instances (731 pending) read back as colorId 5.

15/10/2026: <partner>'s 4 upcoming events colored 6 via the API (read back as 6).

Setup: script.google.com, New project, paste the file, Services + "Google Calendar API", run `install`,
approve. Changed RULES: paste the new file over the old one and run `install` again. Off: run `uninstall`.
