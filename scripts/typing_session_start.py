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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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


GENERAL_THREAD = "1"   # Bot API: typing inside a forum's General topic needs message_thread_id=1


def target(attrs):
    """(chat_id, thread_id) to type in. The bots group's General topic (no message_thread_id on Omer's
    message) types with thread 1: without it Telegram shows "typing" only in the chat list, while the
    topic header keeps its message counter (Omer msg 22689, 06/11/2026)."""
    chat = attrs.get("chat_id")
    thread = attrs.get("message_thread_id")
    if not thread:
        try:
            import auto_ack
            if chat == auto_ack.GENERAL_CHAT_ID:
                thread = GENERAL_THREAD
        except Exception:
            pass
    return chat, thread


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
    ack_key = None
    try:
        import auto_ack
        chans = auto_ack.parse_channels(prompt)
        if chans:
            ack_key = auto_ack.registry_key(chans[0][0])
    except Exception:
        pass
    _, thread = target({"chat_id": chat_id, "message_thread_id": attr("message_thread_id")})
    open_lease(chat_id, msg_id=attr("message_id"), ack_key=ack_key, thread_id=thread)
    _tlog("LEASE-OPEN", "chat_id=" + chat_id + " thread=" + str(thread) + " msg=" + str(attr("message_id")))


if __name__ == "__main__":
    main()
