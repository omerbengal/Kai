#!/usr/bin/env python3
"""
Handle Telegram emoji reactions.

Usage:
  python handle_reaction.py --chat_id ${KAI_TELEGRAM_CHAT_ID} --message_id 123 --reaction 👍 [--mode add|remove]

Called by the Telegram plugin (server.ts) via child_process.exec.
"""
import argparse
import json
import sqlite3
import sys
import io
import urllib.request
from typing import Optional

DB_PATH = 'D:/Kai/kai.db'
BOT_TOKEN = __import__("os").environ["KAI_TELEGRAM_BOT_TOKEN"]
CHAT_ID = __import__("os").environ.get("KAI_TELEGRAM_CHAT_ID", "")


class MessageNotFound(Exception):
    pass


def send_telegram_error(chat_id: str, text: str) -> None:
    try:
        url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
        payload = json.dumps({'chat_id': chat_id, 'text': text}).encode()
        req = urllib.request.Request(url, payload, {'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f'[handle_reaction] failed to send error to Telegram: {e}', file=sys.stderr)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = str
    conn.row_factory = sqlite3.Row
    return conn


def lookup_message(conn: sqlite3.Connection, message_id: int) -> sqlite3.Row:
    row = conn.execute(
        'SELECT id, text, reactions, direction FROM messages WHERE telegram_message_id = ?',
        (message_id,)
    ).fetchone()
    if row is None:
        raise MessageNotFound(f'No message with telegram_message_id={message_id}')
    return row


def update_reactions_add(conn: sqlite3.Connection, msg_id: int, current: Optional[str], emoji: str) -> None:
    reactions = json.loads(current) if current else []
    if emoji not in reactions:
        reactions.append(emoji)
    conn.execute('UPDATE messages SET reactions = ? WHERE id = ?', (json.dumps(reactions), msg_id))


def update_reactions_remove(conn: sqlite3.Connection, msg_id: int, current: Optional[str], emoji: str) -> None:
    reactions = json.loads(current) if current else []
    if emoji in reactions:
        reactions.remove(emoji)
    conn.execute(
        'UPDATE messages SET reactions = ? WHERE id = ?',
        (json.dumps(reactions) if reactions else None, msg_id)
    )


def log_action(conn: sqlite3.Connection, action: str, details: str) -> None:
    conn.execute(
        "INSERT INTO action_log (agent, action, details) VALUES ('listener', ?, ?)",
        (action, details)
    )


def handle_add(message_id: int, reaction: str, chat_id: str) -> None:
    conn = get_connection()
    try:
        msg = lookup_message(conn, message_id)
        update_reactions_add(conn, msg['id'], msg['reactions'], reaction)
        _apply_add_action(conn, msg, reaction)
        log_action(conn, 'reaction_add', f'msg={message_id} emoji={reaction}')
        conn.commit()
    finally:
        conn.close()


def handle_remove(message_id: int, reaction: str, chat_id: str) -> None:
    conn = get_connection()
    try:
        msg = lookup_message(conn, message_id)
        update_reactions_remove(conn, msg['id'], msg['reactions'], reaction)
        _apply_remove_action(conn, msg, reaction)
        log_action(conn, 'reaction_remove', f'msg={message_id} emoji={reaction}')
        conn.commit()
    finally:
        conn.close()


def _apply_add_action(conn: sqlite3.Connection, msg: sqlite3.Row, reaction: str) -> None:
    """Mark reminder/task as done when user reacts."""
    text = msg['text'] or ''
    msg_type, content = _parse_message_type(text)
    if msg_type == 'reminder' and content:
        _mark_reminder(conn, content, 'dismissed')
    elif msg_type == 'task' and content:
        _mark_task(conn, content, 'done')


def _apply_remove_action(conn: sqlite3.Connection, msg: sqlite3.Row, reaction: str) -> None:
    """Reverse reminder/task action when user removes reaction."""
    text = msg['text'] or ''
    msg_type, content = _parse_message_type(text)
    if msg_type == 'reminder' and content:
        _mark_reminder(conn, content, 'fired')
    elif msg_type == 'task' and content:
        _mark_task(conn, content, 'open')


def _parse_message_type(text: str):
    """
    Returns (type, content) where type is 'reminder', 'task', or 'unknown'.
    For reminders: content = reminder message text.
    For tasks: content = task title.
    """
    import re
    if text.startswith('⏰ '):
        return 'reminder', text[len('⏰ '):]
    m = re.match(r'^📋 \[P\d+\] (.+?) — due in ~[\d.]+h$', text)
    if m:
        return 'task', m.group(1)
    m = re.match(r'^⚠️ OVERDUE \[P\d+\]: (.+)$', text)
    if m:
        return 'task', m.group(1)
    return 'unknown', None


def _mark_reminder(conn: sqlite3.Connection, message_text: str, new_status: str) -> None:
    """Mark one-shot reminder with matching message text; skip recurring ones on 'dismissed'."""
    row = conn.execute(
        'SELECT id, recurrence, status FROM reminders WHERE message = ?',
        (message_text,)
    ).fetchone()
    if row is None:
        return
    # For 'dismissed': only apply to one-shot reminders (recurrence IS NULL)
    if new_status == 'dismissed' and row['recurrence'] is not None:
        return
    conn.execute('UPDATE reminders SET status = ? WHERE id = ?', (new_status, row['id']))


def _mark_task(conn: sqlite3.Connection, title: str, new_status: str) -> None:
    """Mark matching task with the given status."""
    conn.execute(
        'UPDATE tasks SET status = ? WHERE title = ? AND status != ?',
        (new_status, title, new_status)
    )


def main():
    # Only replace streams when running as a standalone script (not when imported by tests)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

    parser = argparse.ArgumentParser()
    parser.add_argument('--chat_id', required=True)
    parser.add_argument('--message_id', type=int, required=True)
    parser.add_argument('--reaction', required=True)
    parser.add_argument('--mode', choices=['add', 'remove'], default='add')
    args = parser.parse_args()

    try:
        if args.mode == 'add':
            handle_add(args.message_id, args.reaction, args.chat_id)
        else:
            handle_remove(args.message_id, args.reaction, args.chat_id)
    except MessageNotFound as e:
        send_telegram_error(args.chat_id, f'❌ תגובה: לא נמצאה הודעה {args.message_id} ב-DB')
        print(f'[handle_reaction] {e}', file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        send_telegram_error(args.chat_id, f'❌ שגיאה בטיפול בתגובה: {e}')
        print(f'[handle_reaction] unexpected error: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
