#!/usr/bin/env python
"""
Send a Telegram message SILENTLY (disable_notification=true: no sound, no vibration).

Used ONLY for the dispatcher's transient status ACKs ("🔄 ⏳" and "🎤 מתמלל... ⏳"),
which are edited and deleted moments later. Final replies keep using the reply tool
and keep notifying. The Telegram MCP reply tool does not expose disable_notification,
hence this direct Bot API call.

Prints ONLY the new message_id on stdout (the caller captures it as ACK_MSG_ID).
On failure prints an error to stderr and exits non-zero.

The message is saved into kai.db `messages` exactly like the PostToolUse hook
(save_telegram_message.py --mode outgoing) does for reply-tool sends, because ACK
deletion removes the row by telegram_message_id, and transcribe.py locates a
missing --ack_id by the exact "🎤 מתמלל... ⏳" text.

Typing: this script does NOT touch the typing indicator (investigation #043).
Unlike the reply tool it triggers no PreToolUse typing pause, so typing simply
keeps running through the ACK, which is correct: the ACK is not a final reply.

Usage:
    python D:/Kai/scripts/tg_silent_send.py --text "🔄 ⏳" --reply_to 15203 [--chat_id ${KAI_TELEGRAM_CHAT_ID}]
"""
import argparse
import json
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net_ipv4  # noqa: F401  IPv6 to Telegram is broken here: +10s per call without this

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BOT_TOKEN = os.environ["KAI_TELEGRAM_BOT_TOKEN"]
DB_PATH = "D:/Kai/kai.db"


def send(chat_id, text, reply_to):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_notification": True,
    }
    if reply_to:
        payload["reply_parameters"] = {"message_id": int(reply_to), "allow_sending_without_reply": True}
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return int(data["result"]["message_id"])


def save(message_id, text):
    # Same columns/format as save_telegram_message.py handle_outgoing()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO messages (telegram_message_id, direction, text, reply_to_message_id, reply_to_message_text, ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (message_id, "outgoing", text, None, None, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def main():
    p = argparse.ArgumentParser(description="Silent Telegram send (for transient ACKs only)")
    p.add_argument("--text", required=True)
    p.add_argument("--reply_to", default=None)
    p.add_argument("--chat_id", default=os.environ.get("KAI_TELEGRAM_CHAT_ID", ""))
    args = p.parse_args()

    try:
        message_id = send(args.chat_id, args.text, args.reply_to)
    except Exception as e:
        print(f"tg_silent_send failed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        save(message_id, args.text)
    except Exception as e:
        # The message is out; a DB miss must not make the caller resend it.
        print(f"tg_silent_send: DB save failed: {e}", file=sys.stderr)

    print(message_id)


if __name__ == "__main__":
    main()
