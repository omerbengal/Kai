#!/usr/bin/env python3
"""
Night mode - the single source of truth for Kai's nightly downtime.

Kai and the WhatsApp bridge are shut down at 02:00 and started again at 05:00
LOCAL time, so they spend the night fresh rather than accumulating memory
(investigation: claude.exe reached 512MB after 18 minutes of uptime).

"Local" means scripts/local_tz.py: Israel at home, the destination while travel
mode is on (2026-09-13). The scheduled tasks can no longer carry the clock time,
because it moves with Omer, so:

  KaiNightShutdown / KaiMorningStart  fire every 30 minutes and run
  scripts/kai_night.ps1, which asks this module (--slot) whether now is the
  local 02:00 / 05:00 slot. Only then does it act, and only once per night.

  scripts/kai_watchdog.py / whatsapp_bridge_watchdog.py call should_stand_down(),
  which uses the same local clock for the quiet window.

THE OFF SWITCH
--------------
Night mode only applies while D:\\Kai\\tmp\\night_mode_enabled exists. Delete that
file and the watchdogs never go quiet and kai_night.ps1 does nothing.
Full removal: D:\\Kai\\rollback\\night_mode_2026-08-11\\ROLLBACK.ps1 (after first
running D:\\Kai\\rollback\\travel_mode_<date>\\ROLLBACK.ps1).

WHY THE WATCHDOG WINDOW IS WIDER THAN THE DOWNTIME
--------------------------------------------------
The watchdogs go quiet at 01:55, five minutes before the shutdown, so a check
that happens to land mid-shutdown does not see "Claude is missing" and fight it.
They stay quiet until 05:10, ten minutes after the morning start, so they do not
alarm while the new session is still booting.

ONCE PER NIGHT
--------------
The slot is 30 minutes wide (a trigger fires at :00 and :30, so exactly one
firing lands in it, with slack if Task Scheduler runs a little late). A state
file records the UTC moment each action last ran; an action never runs again
within 12 hours. State is UTC, not "local date", so switching timezone mid-trip
can neither skip a night nor shut Kai down twice.
"""

import json
import os
import sys
from datetime import datetime, time, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import local_tz  # noqa: E402

# --- the window, in LOCAL time (see local_tz.py) ---
SHUTDOWN_AT = time(2, 0)     # kai_night.ps1 -Action Shutdown acts here
STARTUP_AT = time(5, 0)      # kai_night.ps1 -Action Start acts here
SLOT_MINUTES = 30            # width of the acting slot; matches the 30-min trigger

QUIET_FROM = time(1, 55)     # watchdogs stop acting here (5 min before shutdown)
QUIET_UNTIL = time(5, 10)    # watchdogs resume here (10 min after startup)

MIN_GAP = timedelta(hours=12)  # the same action never runs twice within this

ENABLED_FLAG = r"D:\Kai\tmp\night_mode_enabled"
SKIP_LOG = r"D:\Kai\tmp\night_mode.log"
STATE_FILE = r"D:\Kai\tmp\night_slot_state.json"

SLOT_START = {"Shutdown": SHUTDOWN_AT, "Start": STARTUP_AT}


def is_enabled() -> bool:
    """Night mode is opt-in: no flag file means nothing below ever changes behaviour."""
    return os.path.exists(ENABLED_FLAG)


def _between(now: time, start: time, end: time) -> bool:
    """True if now falls in [start, end). Handles a window that crosses midnight."""
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def _local(now=None, cfg=None) -> datetime:
    """Local wall clock. Aware datetimes are converted; naive ones are taken as UTC."""
    return local_tz.now_local(now, cfg)


def in_quiet_window(now: datetime = None, cfg=None) -> bool:
    """True if the watchdogs should stand down right now (local 01:55-05:10)."""
    return _between(_local(now, cfg).time(), QUIET_FROM, QUIET_UNTIL)


def should_stand_down(caller: str, now: datetime = None) -> bool:
    """
    The one call a watchdog needs: True means "return immediately, do nothing".

    Records each stand-down to tmp/night_mode.log rather than the action_log table,
    so the nightly quiet does not bury real events in the DB but is still auditable.
    """
    if not is_enabled():
        return False
    local = _local(now)
    if not _between(local.time(), QUIET_FROM, QUIET_UNTIL):
        return False
    try:
        with open(SKIP_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s stood down (night mode, local %s %s)\n"
                    % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), caller,
                       local.strftime("%H:%M"), local_tz.home_tz_name()))
    except Exception:
        pass  # logging must never be the reason a watchdog crashes
    return True


def _read_state(path=None) -> dict:
    try:
        with open(path or STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(state: dict, path=None) -> None:
    path = path or STATE_FILE
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def slot_decision(action: str, now_utc: datetime = None, state: dict = None, cfg=None):
    """
    Pure decision: (run: bool, reason: str).
    reason is 'run', 'not_in_slot' or 'already_ran'.
    """
    now_utc = local_tz._utc_now(now_utc)
    start = SLOT_START[action]
    end = (datetime.combine(datetime.today(), start) + timedelta(minutes=SLOT_MINUTES)).time()
    local = _local(now_utc, cfg)
    if not _between(local.time(), start, end):
        return False, "not_in_slot"
    last = (state or {}).get(action)
    if last:
        try:
            if now_utc - local_tz.parse_utc(last) < MIN_GAP:
                return False, "already_ran"
        except Exception:
            pass  # unreadable state never blocks the night
    return True, "run"


def claim_slot(action: str, now_utc: datetime = None, dry_run: bool = False, state_path=None, cfg=None):
    """Decide and, when running for real, record the run BEFORE acting (no double shutdown on a retry)."""
    now_utc = local_tz._utc_now(now_utc)
    state = _read_state(state_path)
    run, reason = slot_decision(action, now_utc, state, cfg)
    if run and not dry_run:
        state[action] = local_tz.utc_db_str(now_utc)
        _write_state(state, state_path)
    return run, reason


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:]
    now_arg = None
    if "--now" in argv:
        now_arg = local_tz.parse_utc(argv[argv.index("--now") + 1])
    if "--slot" in argv:
        # Used by kai_night.ps1. Exit 0 = act now, 3 = skip. Prints "RUN|SKIP reason local-time tz".
        action = argv[argv.index("--slot") + 1]
        dry = "--dry-run" in argv
        run, reason = claim_slot(action, now_arg, dry_run=dry)
        local = _local(now_arg)
        print("%s %s local=%s tz=%s%s" % ("RUN" if run else "SKIP", reason, local.strftime("%Y-%m-%d %H:%M"),
                                         local_tz.home_tz_name(now_arg), " dry-run" if dry else ""))
        sys.exit(0 if run else 3)
    elif "--print-window" in argv:
        print("tz=%s" % local_tz.home_tz_name())
        print("shutdown_at=%s" % SHUTDOWN_AT.strftime("%H:%M"))
        print("startup_at=%s" % STARTUP_AT.strftime("%H:%M"))
        print("quiet_from=%s" % QUIET_FROM.strftime("%H:%M"))
        print("quiet_until=%s" % QUIET_UNTIL.strftime("%H:%M"))
        print("enabled=%s" % is_enabled())
    else:
        local = _local(now_arg)
        print("now_local=%s tz=%s" % (local.strftime("%Y-%m-%d %H:%M:%S"), local_tz.home_tz_name(now_arg)))
        print("enabled=%s" % is_enabled())
        print("in_quiet_window=%s" % in_quiet_window(now_arg))
        print("watchdogs_stand_down=%s" % (is_enabled() and in_quiet_window(now_arg)))
