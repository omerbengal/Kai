#!/usr/bin/env python
"""
Keep every Telegram message a script sends in kai.db `messages`.

The reply tool is saved by the PostToolUse hook, but a script that calls the Bot API directly
is invisible to it. Without a row, a reply from Omer to that message arrives with no context
(01/11/2026: he answered scheduled alert 21421 and the dispatcher could not see it).

Rule for any new script that sends through the Bot API: right after the send,
    tg_log.save_response(resp, text)                 # resp = urlopen object, bytes, str or parsed dict
or  tg_log.save_outgoing(message_id, text)           # when the id is already parsed

Same columns and ts format as tg_silent_send.save(). Neither function ever raises: the message
is already out, so a DB problem must not fail or repeat the send. A row that could not be saved
goes to tmp/tg_log_failures.log with its text, so nothing is lost silently.
tests/test_tg_log.py fails on any script that sends without saving.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = "D:/Kai/kai.db"
FAILURE_LOG = "D:/Kai/tmp/tg_log_failures.log"
DB_TIMEOUT_S = 15


def _note_failure(message_id, text, error):
    try:
        print(f"tg_log: message {message_id} not saved: {error}", file=sys.stderr)
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(FAILURE_LOG), exist_ok=True)
        line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "message_id": message_id,
                           "text": text, "error": str(error)}, ensure_ascii=False)
        with open(FAILURE_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def save_outgoing(message_id, text, reply_to=None):
    """Insert one outgoing row. Returns True if saved. Never raises."""
    conn = None
    try:
        if not message_id:
            return False
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
        conn.execute(
            "INSERT INTO messages (telegram_message_id, direction, text, reply_to_message_id, "
            "reply_to_message_text, ts) VALUES (?, ?, ?, ?, ?, ?)",
            (int(message_id), "outgoing", text, int(reply_to) if reply_to else None, None,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return True
    except Exception as e:
        _note_failure(message_id, text, e)
        return False
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def save_response(resp, text, reply_to=None):
    """Save from a Bot API response: an urlopen object, bytes, str or parsed dict. Never raises."""
    try:
        data = resp
        if hasattr(data, "read"):
            data = data.read()
        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8")
        if isinstance(data, str):
            data = json.loads(data)
        if not (isinstance(data, dict) and data.get("ok")):
            return False  # Telegram did not accept it, so there is no message to record
        message_id = data["result"]["message_id"]
    except Exception as e:
        _note_failure(None, text, e)
        return False
    return save_outgoing(message_id, text, reply_to)
