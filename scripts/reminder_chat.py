#!/usr/bin/env python3
"""Where the fired ⏰ reminders go, and the chat-keyed message rows for the bots group.

Omer, 10/11/2026 (General msg 1613/1616): reminders fire in the bots group's General topic
instead of the private chat. One switch, one file:

    python D:/Kai/scripts/reminder_chat.py general   # ⏰ to General (-1004297864522, no thread)
    python D:/Kai/scripts/reminder_chat.py private   # back to the private chat
    python D:/Kai/scripts/reminder_chat.py status

config/reminder_chat.json {"target": "general" | "private"}; missing / broken file = private.

Why a separate table (chat_messages) and not group rows in `messages`:
Telegram message ids are per chat, so General ids collide with private ids. `messages` has no
chat column and many paths look rows up - and DELETE them (ACK cleanup) - by telegram_message_id
alone. A General ⏰ row there could be dismissed by a 👍 on a private message with the same id, or
deleted with a private ACK. chat_messages is keyed by (chat_id, telegram_message_id), so a group
lookup can only ever match the group's own row, and nothing private ever reads it.
"""
import json
import os
import sys

PRIVATE_CHAT = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
GENERAL_CHAT = "-1004297864522"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "config", "reminder_chat.json")
TARGETS = {"general": GENERAL_CHAT, "private": PRIVATE_CHAT}

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    telegram_message_id INTEGER NOT NULL,
    direction TEXT NOT NULL DEFAULT 'outgoing',
    text TEXT,
    reply_to_message_id INTEGER,
    reactions TEXT,
    reminder_id INTEGER,
    task_id INTEGER,
    ts DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (chat_id, telegram_message_id)
)
"""


def is_group(chat_id):
    """Group / supergroup ids are negative; the private chat is positive."""
    return str(chat_id or "").strip().startswith("-")


def target(path=None):
    """'general' or 'private'. Never raises; anything unreadable = private (the old behaviour)."""
    try:
        with open(path or CONFIG_PATH, encoding="utf-8") as f:
            t = str(json.load(f).get("target", "private")).strip().lower()
        return t if t in TARGETS else "private"
    except Exception:
        return "private"


def reminder_chat_id(path=None):
    return TARGETS[target(path)]


def set_target(name, path=None):
    name = name.strip().lower()
    if name not in TARGETS:
        raise ValueError(f"target must be one of {sorted(TARGETS)}")
    p = path or CONFIG_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"target": name}, f)
    return name


def ensure_table(conn):
    conn.execute(SCHEMA)


def save_row(conn, chat_id, tg_msg_id, text, link=None, reply_to=None, direction="outgoing"):
    """Insert / replace the (chat_id, message_id) row. link = ('reminder_id'|'task_id', value). Never raises."""
    if conn is None or not tg_msg_id:
        return
    try:
        ensure_table(conn)
        rid = tid = None
        if link and link[0] == "reminder_id":
            rid = link[1]
        elif link and link[0] == "task_id":
            tid = link[1]
        conn.execute(
            "INSERT OR REPLACE INTO chat_messages (chat_id, telegram_message_id, direction, text, "
            "reply_to_message_id, reminder_id, task_id, ts) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (str(chat_id), int(tg_msg_id), direction, text, int(reply_to) if reply_to else None, rid, tid))
    except Exception:
        pass


def get_row(conn, chat_id, tg_msg_id):
    """(id, telegram_message_id, text, reactions, reminder_id, task_id) or None."""
    try:
        ensure_table(conn)
        return conn.execute(
            "SELECT id, telegram_message_id, text, reactions, reminder_id, task_id FROM chat_messages "
            "WHERE chat_id = ? AND telegram_message_id = ?", (str(chat_id), int(tg_msg_id))).fetchone()
    except Exception:
        return None


def delete_row(conn, chat_id, tg_msg_id):
    try:
        ensure_table(conn)
        conn.execute("DELETE FROM chat_messages WHERE chat_id = ? AND telegram_message_id = ?",
                     (str(chat_id), int(tg_msg_id)))
    except Exception:
        pass


def reminder_for_message(conn, chat_id, tg_msg_id):
    """reminder_id linked to the ⏰ (or its contact card) message in that chat, else None."""
    try:
        if is_group(chat_id):
            row = get_row(conn, chat_id, tg_msg_id)
            return row[4] if row else None
        row = conn.execute(
            "SELECT reminder_id FROM messages WHERE telegram_message_id = ? AND reminder_id IS NOT NULL "
            "ORDER BY id DESC LIMIT 1", (int(tg_msg_id),)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def main(argv):
    cmd = (argv[1] if len(argv) > 1 else "status").lower()
    if cmd == "status":
        t = target()
        print(f"TARGET={t} CHAT_ID={TARGETS[t]}")
        return 0
    if cmd in TARGETS:
        set_target(cmd)
        print(f"TARGET={cmd} CHAT_ID={TARGETS[cmd]}")
        return 0
    print("usage: reminder_chat.py general|private|status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
