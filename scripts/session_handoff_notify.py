#!/usr/bin/env python3
"""
session_handoff_notify.py
Reads D:/Kai/tmp/session_handoff.md (if it exists) and prints it to stdout
so the new Claude session sees the prior conversation context at startup.
Called from SessionStart hook (async: false).

The morning briefing is owned by the KaiMorningBriefing Windows scheduled
task (scripts/run_morning_briefing.py), which fires at 09:00 sharp. This
script no longer asks Claude to register a duplicate cron job for it.
"""

import sys
import os

# Force stdout to UTF-8 so Hebrew + emoji print correctly on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
else:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HANDOFF_FILE = "D:/Kai/tmp/session_handoff.md"

STARTUP_ACTIONS = ""  # (a personal page-refresh job was removed from the public version)


def announce_startup_actions():
    """Tell the new session to re-register the page-refresh cron."""
    ids_path = "D:/Kai/.kai_cron_ids"
    try:
        with open(ids_path, "r", encoding="utf-8") as f:
            registered = bool(f.read())
    except OSError:
        registered = False
    # The id from the dead session is stale by definition, so its presence is
    # not proof of a live job. Announce regardless; re-registering is cheap and
    # a missing refresh job is silent.
    del registered
    print(STARTUP_ACTIONS)


def announce_timezone():
    """Travel mode: tell the new session which timezone Omer is in (never assume UTC+3)."""
    try:
        sys.path.insert(0, "D:/Kai/scripts")
        import local_tz
        print(local_tz.session_context_line())
    except Exception as e:
        print(f"=== TIMEZONE === could not read travel mode ({e}); run python D:/Kai/scripts/travel_mode.py status")


def main():
    announce_timezone()
    announce_startup_actions()

    if not os.path.exists(HANDOFF_FILE):
        return

    with open(HANDOFF_FILE, "r", encoding="utf-8") as f:
        content = f.read().strip()

    if not content:
        os.remove(HANDOFF_FILE)
        return

    print("")
    print("=== הקשר מהסשן הקודם ===")
    print(content)
    print("=========================")

    try:
        os.remove(HANDOFF_FILE)
    except Exception:
        pass


if __name__ == "__main__":
    main()
