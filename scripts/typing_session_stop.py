#!/usr/bin/env python
"""
Stop hook — stop the persistent session typing indicator started by
typing_session_start.py when the assistant finishes its turn.

Paired with typing_session_start.py (bug 2026-07-23). Idempotent and safe to call
when nothing is running. Never blocks the turn (exits 0 fast); ignores
stop_hook_active — we run a command, we don't block Claude from stopping.
"""
import sys
import os
import subprocess

def _tlog(event, extra=""):
    """Timestamped trace of typing-hook activity (investigation: missing indicator)."""
    try:
        import datetime
        stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = "{}  {:<14} {}".format(stamp, event, extra)
        with open("D:/Kai/tmp/typing_trace.log", "a", encoding="utf-8") as f:
            f.write(line + chr(10))
    except Exception:
        pass


CREATE_NO_WINDOW = 0x08000000
STOP_TYPING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stop_typing.py")
SESSION_KEY = "session"
FLAG_FILE = "D:/Kai/tmp/typing_resume_armed.json"


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from typing_lease import pause, close
    if "--end-turn" in sys.argv:
        close()          # Stop hook: turn over, drop the lease entirely
        _tlog("LEASE-CLOSE")
    elif "--react" in sys.argv:
        # PreToolUse(react): a reaction does not clear client-side typing, so
        # also wait until the ~5s typing status has expired (<= TYPING_VISIBLE).
        pause(wait_expire=True)
        _tlog("LEASE-PAUSE", "react (waited for typing to expire)")
    else:
        pause()          # PreToolUse(reply): typing off now, turn stays open
        _tlog("LEASE-PAUSE")


if __name__ == "__main__":
    main()
