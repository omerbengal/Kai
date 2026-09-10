#!/usr/bin/env python
"""
PreToolUse(any tool) hook — FIRE an armed typing resume.

Paired with typing_session_resume.py. A tool call after a reply is proof that the
assistant is still working, so this is the moment typing should come back. If the
turn ended instead, the Stop hook already removed the flag and nothing happens.

This replaces the old delay-race resume (see typing_session_resume.py docstring):
typing after the FINAL reply is now structurally impossible, and typing after an
intermediate reply resumes with no visible gap.

Skips the Telegram reply tool itself — PreToolUse(reply) already runs
typing_session_stop.py, and re-starting typing there would just fight it.
"""
import sys
import os
import json
import subprocess

def _tlog(event, extra=""):
    try:
        import datetime
        stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = "{}  {:<14} {}".format(stamp, event, extra)
        with open("D:/Kai/tmp/typing_trace.log", "a", encoding="utf-8") as f:
            f.write(line + chr(10))
    except Exception:
        pass


CREATE_NO_WINDOW = 0x08000000
START_TYPING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "start_typing.py")
FLAG_FILE = "D:/Kai/tmp/typing_resume_armed.json"
SESSION_KEY = "session"
SKIP_TOOLS = ("mcp__plugin_telegram_telegram__reply", "mcp__plugin_telegram_telegram__react")


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if (data.get("tool_name", "") or "") in SKIP_TOOLS:
        return
    # Renew only - never opens a lease. If no lease is open (between turns,
    # or a background agent working after the turn ended) this is a no-op.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from typing_lease import renew
    renew()


if __name__ == "__main__":
    main()
