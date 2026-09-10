#!/usr/bin/env python3
"""
Handle Telegram emoji reactions.

Usage:
  python handle_reaction.py --chat_id ${KAI_TELEGRAM_CHAT_ID} --message_id 123 --reaction 👍 [--mode add|remove]

Called by the Telegram plugin (server.ts) via child_process.exec.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import io
import urllib.request
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from heartbeat import advance_recurring  # noqa: E402  (shared recurrence arithmetic)

DB_PATH = 'D:/Kai/kai.db'
BOT_TOKEN = os.environ["KAI_TELEGRAM_BOT_TOKEN"]


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
        'SELECT id, telegram_message_id, text, reactions, direction FROM messages WHERE telegram_message_id = ?',
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


def log_reaction(conn: sqlite3.Connection, message_id: int, emoji: str, action: str) -> None:
    conn.execute(
        "INSERT INTO message_reactions (telegram_message_id, direction, emoji, action, ts) "
        "VALUES (?, 'incoming', ?, ?, datetime('now'))",
        (message_id, emoji, action)
    )


def handle_add(message_id: int, reaction: str, chat_id: str) -> None:
    conn = get_connection()
    try:
        msg = lookup_message(conn, message_id)
        update_reactions_add(conn, msg['id'], msg['reactions'], reaction)
        _apply_add_action(conn, msg, reaction)
        log_action(conn, 'reaction_add', f'msg={message_id} emoji={reaction}')
        log_reaction(conn, message_id, reaction, 'add')
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
        log_reaction(conn, message_id, reaction, 'remove')
        conn.commit()
    finally:
        conn.close()


def _lookup_by_fk(conn: sqlite3.Connection, telegram_message_id: int):
    """
    Find reminder or task directly from messages.reminder_id / messages.task_id.
    Returns ('reminder', row) | ('task', row) | (None, None).
    """
    msg = conn.execute(
        "SELECT reminder_id, task_id FROM messages WHERE telegram_message_id = ?",
        (telegram_message_id,)
    ).fetchone()
    if msg is None:
        return None, None

    if msg['reminder_id']:
        reminder = conn.execute("SELECT * FROM reminders WHERE id = ?", (msg['reminder_id'],)).fetchone()
        if reminder:
            return 'reminder', reminder

    if msg['task_id']:
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (msg['task_id'],)).fetchone()
        if task:
            return 'task', task

    return None, None


def _apply_add_action(conn: sqlite3.Connection, msg: sqlite3.Row, reaction: str) -> None:
    """Mark reminder/task as done when user reacts."""
    # Try FK-based lookup first (fast path for messages sent after migration)
    entity_type, entity_row = _lookup_by_fk(conn, msg['telegram_message_id'])
    if entity_type == 'reminder':
        _mark_reminder_by_row(conn, entity_row, 'dismissed')
        return
    if entity_type == 'task':
        _mark_task_by_row(conn, entity_row, 'done')
        return
    # Fallback: text-prefix matching (for older messages without notification_message_id)
    text = msg['text'] or ''
    msg_type, content = _parse_message_type(text)
    if msg_type == 'reminder' and content:
        _mark_reminder(conn, content, 'dismissed')
    elif msg_type == 'task' and content:
        _mark_task(conn, content, 'done')


def _apply_remove_action(conn: sqlite3.Connection, msg: sqlite3.Row, reaction: str) -> None:
    """Reverse reminder/task action when user removes reaction."""
    # Try FK-based lookup first (fast path for messages sent after migration)
    entity_type, entity_row = _lookup_by_fk(conn, msg['telegram_message_id'])
    if entity_type == 'reminder':
        _mark_reminder_by_row(conn, entity_row, 'fired')
        return
    if entity_type == 'task':
        _mark_task_by_row(conn, entity_row, 'open')
        return
    # Fallback: text-prefix matching (for older messages without notification_message_id)
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
    if text.startswith('⏰ '):
        return 'reminder', text[len('⏰ '):]
    m = re.match(r'^📋 \[P\d+\] (.+?) — due in ~[\d.]+h$', text)
    if m:
        return 'task', m.group(1)
    m = re.match(r'^⚠️ OVERDUE \[P\d+\]: (.+)$', text)
    if m:
        return 'task', m.group(1)
    return 'unknown', None


def _mark_reminder_by_row(conn: sqlite3.Connection, row: sqlite3.Row, new_status: str) -> None:
    """Mark a reminder directly by its DB row."""
    if row['recurrence'] is not None:
        _ack_recurring(conn, row, new_status)
        return
    if new_status == 'fired':
        # Un-react: re-start the nag cycle immediately
        conn.execute(
            "UPDATE reminders SET status = 'pending', trigger_at = datetime('now') WHERE id = ?",
            (row['id'],)
        )
    else:
        conn.execute('UPDATE reminders SET status = ? WHERE id = ?', (new_status, row['id']))


def _mark_task_by_row(conn: sqlite3.Connection, row: sqlite3.Row, new_status: str) -> None:
    """Mark a task directly by its DB row.
    Only transitions: open→done (add reaction) or done→open (remove reaction).
    Does not touch cancelled tasks.
    """
    expected_current = 'open' if new_status == 'done' else 'done'
    if new_status == 'done':
        conn.execute(
            "UPDATE tasks SET status = ?, completed_at = datetime('now') WHERE id = ? AND status = ?",
            (new_status, row['id'], expected_current)
        )
    else:
        conn.execute(
            'UPDATE tasks SET status = ?, completed_at = NULL WHERE id = ? AND status = ?',
            (new_status, row['id'], expected_current)
        )


def _ack_recurring(conn: sqlite3.Connection, row: sqlite3.Row, new_status: str) -> None:
    """Apply a reaction to a recurring reminder.

    Reacting means "handled today's occurrence" — not "cancel the series". So the reminder
    stops nagging and rolls forward to its next occurrence. Removing the reaction undoes
    that and resumes nagging right away.
    """
    if new_status == 'fired':
        conn.execute(
            "UPDATE reminders SET status = 'pending', trigger_at = datetime('now'), "
            "series_at = NULL, nag_count = 0 WHERE id = ?",
            (row['id'],)
        )
        return

    # Only advance an occurrence that is still the one being nagged about.
    #
    # heartbeat.py gives up after MAX_RECURRING_NAGS and advances the series itself,
    # clearing series_at and leaving trigger_at on the NEXT occurrence. A reaction that
    # arrives after that point used to advance again from that already-advanced time,
    # so the series silently skipped a day: reminder 149 was acknowledged 34 minutes
    # after the nag cap on 2026-08-18 and jumped from the 19th to the 20th, quietly
    # losing a day. series_at is None with a future trigger_at exactly when the cap has
    # already done the work - then there is nothing left to advance.
    already_advanced = (
        row['series_at'] is None
        and conn.execute("SELECT ? > datetime('now')", (row['trigger_at'],)).fetchone()[0]
    )
    if already_advanced:
        conn.execute(
            "UPDATE reminders SET status = 'pending', nag_count = 0 WHERE id = ?",
            (row['id'],)
        )
        return

    advance_recurring(conn, row['id'], row['recurrence'], row['series_at'] or row['trigger_at'])


def _mark_reminder(conn: sqlite3.Connection, message_text: str, new_status: str) -> None:
    """Mark the reminder with matching message text (text-prefix fallback path)."""
    row = conn.execute(
        'SELECT id, recurrence, status, trigger_at, series_at FROM reminders WHERE message = ?',
        (message_text,)
    ).fetchone()
    if row is None:
        return
    if row['recurrence'] is not None:
        _ack_recurring(conn, row, new_status)
        return
    if new_status == 'fired':
        # Un-react: re-start the nag cycle immediately
        conn.execute(
            "UPDATE reminders SET status = 'pending', trigger_at = datetime('now') WHERE id = ?",
            (row['id'],)
        )
    else:
        conn.execute('UPDATE reminders SET status = ? WHERE id = ?', (new_status, row['id']))


def _mark_task(conn: sqlite3.Connection, title: str, new_status: str) -> None:
    """Mark matching task with the given status.
    Only transitions: open→done (add reaction) or done→open (remove reaction).
    Does not touch cancelled tasks.
    """
    expected_current = 'open' if new_status == 'done' else 'done'
    if new_status == 'done':
        conn.execute(
            "UPDATE tasks SET status = ?, completed_at = datetime('now') WHERE title = ? AND status = ?",
            (new_status, title, expected_current)
        )
    else:
        conn.execute(
            'UPDATE tasks SET status = ?, completed_at = NULL WHERE title = ? AND status = ?',
            (new_status, title, expected_current)
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
