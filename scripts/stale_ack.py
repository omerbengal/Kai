#!/usr/bin/env python
"""
Safety net for an ACK left on "⏳ מייצר agent..." with no agent behind it (Omer msg 21171, 31/10/2026).

Called once a minute from heartbeat.py. The normal cure is `dispatch_start.py --queued` on a follow-up to a
running worker; this only catches what slips past it (a dispatcher that used plain --title, a failed spawn).

Fresh spawns reach their first edit in 55s at worst (178 spawns measured), so 2 minutes means nobody started.
ack_log alone cannot prove that, because workers often skip log_ack, so an ACK is edited only when the
session transcripts also show no edit_message on it. Any doubt = leave the message alone. Each ACK is
edited once at most: the logged edit makes it stop matching.
"""
import glob
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

DB_PATH = "D:/Kai/kai.db"
TRANSCRIPTS = "C:/Users/Omer/.claude/projects/D--Kai"
CHAT_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
SPAWN_SUFFIX = "⏳ מייצר agent..."
STUCK_SUFFIX = "📋 מחכה ל-agent, הוא עוד לא התחיל"
GRACE_SECONDS = 120
MAX_AGE_SECONDS = 1800  # older rows are history, never touch them
MAIN_TAIL_BYTES = 5_000_000  # the main session transcript is huge; the ACK is recent


def _candidates(conn, now):
    """ACKs whose LATEST ack_log row is still the standard spawning text, inside the age window."""
    fmt = "%Y-%m-%d %H:%M:%S"
    newest = (now - timedelta(seconds=GRACE_SECONDS)).strftime(fmt)
    oldest = (now - timedelta(seconds=MAX_AGE_SECONDS)).strftime(fmt)
    rows = conn.execute(
        """SELECT a.ack_msg_id, a.text FROM ack_log a
           JOIN (SELECT ack_msg_id, MAX(id) AS last_id FROM ack_log WHERE ts >= ? GROUP BY ack_msg_id) m
             ON a.id = m.last_id
           WHERE a.ts <= ?""", (oldest, newest)).fetchall()
    return [(a, t) for a, t in rows if t and t.startswith("🔄") and t.endswith(SPAWN_SUFFIX)]


def _transcript_edited(ack_id, root):
    """True when any recent transcript edits this message, or when transcripts cannot be read."""
    if not os.path.isdir(root):
        return True
    pattern = re.compile(rb'"message_id":\s*"?%d\b' % ack_id)
    cutoff = time.time() - 2 * MAX_AGE_SECONDS
    try:
        for path in glob.glob(os.path.join(root, "*", "subagents", "*.jsonl")):
            if os.path.getmtime(path) >= cutoff:
                with open(path, "rb") as f:
                    if pattern.search(f.read()):
                        return True
        for path in glob.glob(os.path.join(root, "*.jsonl")):
            if os.path.getmtime(path) >= cutoff:
                with open(path, "rb") as f:
                    f.seek(max(0, os.path.getsize(path) - MAIN_TAIL_BYTES))
                    if pattern.search(f.read()):
                        return True
    except OSError:
        return True
    return False


def _telegram_edit(message_id, text):
    import dispatch_start  # lazy: a heartbeat with no candidate never loads the Telegram stack
    dispatch_start.edit_text(CHAT_ID, message_id, text)


def fix_stale(now=None, db_path=DB_PATH, transcripts=TRANSCRIPTS, edit=_telegram_edit):
    """Returns how many ACKs were edited."""
    now = now or datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.text_factory = str
    done = 0
    try:
        for ack_id, text in _candidates(conn, now):
            if _transcript_edited(ack_id, transcripts):
                continue
            new_text = text[: -len(SPAWN_SUFFIX)] + STUCK_SUFFIX
            try:
                edit(ack_id, new_text)
                done += 1
            except Exception as e:  # already deleted, network: log below anyway, one attempt only
                print(f"stale_ack: edit {ack_id} failed: {e}")
            conn.execute("INSERT INTO ack_log (ack_msg_id, action, text, ts) VALUES (?, 'edited', ?, ?)",
                         (ack_id, new_text, now.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
    finally:
        conn.close()
    return done


if __name__ == "__main__":
    print(fix_stale())
