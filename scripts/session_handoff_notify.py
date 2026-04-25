#!/usr/bin/env python3
"""
session_handoff_notify.py
Reads D:/Kai/tmp/session_handoff.md (if it exists) and prints it to stdout
so the new Claude session sees the prior conversation context at startup.
Called from SessionStart hook (async: false).

Always emits the STARTUP ACTIONS REQUIRED block. Claude Code's cron store
(.claude/scheduled_tasks.json) does NOT survive a restart even with
durable=true, so the morning briefing cron must be re-registered at the
beginning of every fresh session. session_start_cleanup.py deletes the
stale cron_registered_sentinel from the previous session to keep the flow
honest; the sentinel is now purely a session-scoped marker (created by
Claude after CronCreate succeeds) that the listener/dispatcher can use to
avoid re-triggering CronCreate within the same session.
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


def main():
    # Always print startup actions on session start. The SessionStart hook
    # only fires once per session, so this block is injected into session
    # context exactly once — no need to gate on sentinel existence.
    # Cron state in Claude Code does not survive restarts, so every fresh
    # session must re-register the morning briefing.
    print("=== STARTUP ACTIONS REQUIRED ===")
    print("Immediately call CronCreate to register the morning briefing cron job:")
    print("  cron='0 9 * * *' (local Israel time = 09:00 Jerusalem), prompt='D:/Kai/agents/morning_briefing_launcher.md', recurring=true, durable=true")
    print("Save the returned ID to D:/Kai/.kai_cron_ids (update morning_briefing_cron_id line).")
    print("Note: heartbeat runs via Windows Task Scheduler — do NOT register it as a cron.")
    print("After saving the ID, create D:/Kai/tmp/cron_registered_sentinel (touch the file) as a session-scoped marker.")
    print("=================================")

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
