# Travel mode ("מצב חו״ל") - design + implementation plan

Approved by Omer 2026-09-13 (callback `travel_mode_build` on msg 13854). Example destination: Thailand (Asia/Bangkok).

## Approved design (as sent, msg 13854)

1. `config/travel_mode.json` - destination, timezone, end date
2. `scripts/local_tz.py` - returns the current home timezone (Israel or destination), DST handled by zoneinfo
3. Night mode - the two night tasks fire every 30 min; the script decides whether it is now the local 02:00 / 05:00 slot. Watchdogs compute the quiet window the same way
4. Briefing - fires every 30 min (design said hourly; 30 min also covers half-hour zones such as India), sends only at local 09:xx, dedupe on the LOCAL date; weather for the destination city
5. Reminders and times - "מחר ב-08:00" is interpreted in local time and displayed as "08:00 (תאילנד)" abroad
6. Kai gets the active timezone at every session start instead of "always UTC+3"

Activation: "מצב חו״ל תאילנד עד DD/MM"; "חזרתי" = back to Israel; on the end date it reverts automatically with a Telegram message.

Follow-up (msg 13858): while abroad every time Omer says is destination-local; "שעון ישראל" forces Israel time; after returning, existing reminders keep their absolute UTC moment (nothing moves).

## Implementation

### Single source of truth: `scripts/local_tz.py`
- `load_config()` - never raises; bad/missing file = inactive
- `is_active(now_utc)` - `active` true, tz loads, and destination-local date <= `end` (end date is INCLUSIVE: "עד DD/MM" = destination time through DD/MM 23:59 Thailand time, Israel time from 26/09 00:00 Thailand time)
- `home_tz()`, `home_tz_name()`, `now_local()`, `tz_label()` ("תאילנד" / "")
- `to_utc(naive_local, force_israel=False)`, `local_str_to_utc_str("YYYY-MM-DD HH:MM")` -> `"YYYY-MM-DD HH:MM:SS"` UTC (space format - the heartbeat compares strings)
- `parse_utc(str)` accepts `T`/`Z`/space formats; `fmt_local(utc, fmt)` -> `"12:00 (תאילנד)"` abroad, `"12:00"` at home
- `sqlite_offset_modifier()` -> e.g. `"+420 minutes"` for SQL date math
- `session_context_line()` - text injected at SessionStart
- Fail-safe: any config/zoneinfo error -> Asia/Jerusalem; if even that cannot load (tzdata missing) -> fixed UTC+3 (the legacy behaviour)
- `tzdata` pip-installed into both Python39 (fast scripts, watchdogs, briefing, kai_night.ps1) and `.venv` (heartbeat)

### `scripts/travel_mode.py` CLI
`on --dest תאילנד --tz Asia/Bangkok [--dest_en Thailand] [--end YYYY-MM-DD|DD/MM] [--weather Bangkok]`, `off`, `status [--json]`, `schedule`, `expire-check` (called by heartbeat each minute: when the end date passed, deactivates and sends a Telegram message once).

### Night mode (item 3)
- `night_mode.py` gains `slot_decision(action, now_utc)`: Shutdown runs only in local [02:00, 02:30), Start only in local [05:00, 05:30), and only if the same action did not run in the last 12 hours (state: `tmp/night_slot_state.json`, UTC timestamps - not keyed by local date, so a timezone switch mid-trip can never skip or double a night).
- `night_mode.py --slot Shutdown|Start [--dry-run] [--now ISO_UTC]` exits 0 = RUN (records state unless dry-run), 3 = SKIP.
- `kai_night.ps1` calls the gate after the flag check; new `-DryRun` switch evaluates the gate and exits before touching any process. `-Force` still bypasses flag and gate.
- Tasks `KaiNightShutdown` / `KaiMorningStart`: daily trigger + repetition PT30M for P1D (XML re-import as the same user; no elevation needed since RunLevel is LeastPrivilege).
- Watchdog quiet window = local 01:55-05:10 (watchdog tasks themselves unchanged - they need elevation).
- Flag file `tmp/night_mode_enabled` stays the off switch for everything.

### Briefing (item 4)
- `run_morning_briefing.py`: `briefing_gate(now_local, already_sent)` - send only when local hour == 9; dedupe on local date via `action_log.created_at` shifted by the local offset. `--dry-run`, `--now ISO_UTC`, `--force` (force skips the gate and dedupe, as before).
- Weather: Tel Aviv at home; abroad, `weather_city` geocoded via Open-Meteo geocoding API.
- All SQL `'+3 hours'` replaced by the live local offset; header shows a travel-mode line abroad.
- Task `KaiMorningBriefing`: repetition PT30M for P1D.

### Heartbeat
Sleep window 01:00-05:59 in local time; runs `travel_mode.expire_check()` each minute (cheap file read).

### Reminders / tasks / calendar (item 5)
- `fast_reminder_create.py`, `fast_reminder_update.py`: new `--local_at "YYYY-MM-DD HH:MM"` (+ `--israel_time`) converted via local_tz; `--trigger_at` (UTC) still accepted. Display via `fmt_local`.
- `fast_task_create.py`: new `--due_local` (+ `--israel_time`); `fast_task_update.py` `--due_at` and `fast_task_done.py` `--completed_at` are local time (were "Jerusalem") with `--israel_time`.
- `fast_tasks_list.py`, `fast_reminder_list.py`: local-day ranges and display, label in header abroad.
- `_gcal.py`: event dateTime built with the real offset of the home tz and `timeZone` = home tz name (was hard-coded `+03:00`); display in home tz. `fast_cal_list/update/delete` use `now_local()`.
- `fast_wa_history.py`, `fast_wa_search.py`: message timestamps displayed in home tz.
- `session_start_cleanup.py`: silent "קלוד עלה" window follows local time.

### Session context (item 6)
`session_handoff_notify.py` (SessionStart, sync) prints `local_tz.session_context_line()`. CLAUDE.md, agents/listener.md, agents/worker.md: "use scripts/local_tz.py; never assume UTC+3". listener.md Option D: "מצב חו״ל <יעד> [עד DD/MM]" / "חזרתי" -> `scripts/travel_mode.py`.

### Out of scope (still hard-coded UTC+3, deliberately untouched)
- `scripts/train_search.py`, `scripts/fast/fast_train_search.py` (Israel Railways - Israel time is correct)
- `scripts/fast/fast_action_log.py`, `scripts/fast/fast_memory_save.py`, `scripts/on_compact.py`, `scripts/write_session_handoff.py` (logging / internal timestamps)
- `scripts/fast/_gcal.py` stays compatible with its `parse_datetime_jerusalem` name (now home-tz aware)

## Rollback
`D:\Kai\rollback\travel_mode_<date>\ROLLBACK.ps1` - restores every modified file and the three task XMLs. Quick off switch without rollback: `python D:/Kai/scripts/travel_mode.py off` (everything reverts to Israel time; the 30-min triggers keep working in Israel time).
