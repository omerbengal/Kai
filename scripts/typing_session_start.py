#!/usr/bin/env python
"""
UserPromptSubmit hook — start a persistent 'typing' indicator for the whole time
the main session works on an incoming Telegram message.

Problem (bug 2026-07-23): for main-session / "thinking" work (no worker spawned)
nothing kept the Telegram typing indicator alive, so Omer saw it flicker for a few
seconds and vanish while waiting. This hook starts a persistent typing loop the
moment a Telegram message arrives; the paired Stop hook (typing_session_stop.py)
stops it when the assistant finishes the turn.

Keyed by the fixed worker_id "session" so it runs independently of the
ORIGINAL_MSG_ID-keyed loops that fast scripts / workers manage (both just send the
same 'typing' action — harmless overlap). typing_loop.py caps itself at 10 min, so
a missed Stop can never orphan indefinitely (investigation #016).

Reads the hook payload (JSON) on stdin; only acts on Telegram channel messages.
"""
import sys
import os
import json
import re
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
START_TYPING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "start_typing.py")
SESSION_KEY = "session"


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    prompt = data.get("prompt", "") or data.get("content", "")
    if not prompt:
        _tlog("start-skip", "no prompt")
        return

    m = re.search(r"<channel([\s\S]*?)>", prompt)
    if not m:
        _tlog("start-skip", "no <channel> tag")
        return
    attrs = m.group(1)

    def attr(name):
        mm = re.search(f'{name}="(.*?)"', attrs, re.DOTALL)
        return mm.group(1) if mm else None

    source = attr("source") or ""
    if "telegram" not in source:
        _tlog("start-skip", "source=" + source)
        return
    chat_id = attr("chat_id")
    if not chat_id:
        _tlog("start-skip", "no chat_id")
        return
    # Skip pure reaction events (frequent 👍 acks that usually need no work).
    if attr("event_type") == "reaction":
        _tlog("start-skip", "reaction event")
        return

    # The ONLY entry point allowed to OPEN the indicator is an incoming message
    # from Omer. No agent, main or background, can switch typing on from nothing
    # (investigation #043).
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from typing_lease import open_lease
    open_lease(chat_id)
    _tlog("LEASE-OPEN", "chat_id=" + chat_id + " msg=" + str(attr("message_id")))


if __name__ == "__main__":
    main()
