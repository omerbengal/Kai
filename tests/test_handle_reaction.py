"""Tests for handle_reaction.py — run with: python -m pytest D:/Kai/tests/test_handle_reaction.py -v"""
import json
import sqlite3
import sys

import pytest

# Add scripts dir to path
sys.path.insert(0, 'D:/Kai/scripts')


def make_db(tmp_path):
    """Create a temp DB with the messages schema."""
    db_path = str(tmp_path / 'test.db')
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_message_id INTEGER,
            direction TEXT NOT NULL,
            text TEXT,
            reactions TEXT,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE reminders (
            id INTEGER PRIMARY KEY,
            message TEXT NOT NULL,
            trigger_at DATETIME NOT NULL,
            recurrence TEXT,
            status TEXT DEFAULT 'pending'
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            priority INTEGER DEFAULT 5,
            status TEXT DEFAULT 'open'
        );
        CREATE TABLE action_log (
            id INTEGER PRIMARY KEY,
            agent TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def test_reaction_added_to_messages(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (42, 'outgoing', 'hello')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_add(message_id=42, reaction='👍', chat_id='123')

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT reactions FROM messages WHERE telegram_message_id = 42").fetchone()
    conn.close()
    assert json.loads(row[0]) == ['👍']


def test_second_reaction_appended(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text, reactions) VALUES (42, 'outgoing', 'hello', '[\"❤️\"]')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_add(message_id=42, reaction='👍', chat_id='123')

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT reactions FROM messages WHERE telegram_message_id = 42").fetchone()
    conn.close()
    assert set(json.loads(row[0])) == {'❤️', '👍'}


def test_message_not_found_raises(tmp_path):
    db = make_db(tmp_path)
    import handle_reaction
    handle_reaction.DB_PATH = db
    with pytest.raises(handle_reaction.MessageNotFound):
        handle_reaction.handle_add(message_id=999, reaction='👍', chat_id='123')


def test_reaction_on_oneshot_reminder_dismisses(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (10, 'outgoing', '⏰ לשלוח הודעה לרופא')")
    conn.execute("INSERT INTO reminders (message, trigger_at, recurrence, status) VALUES ('לשלוח הודעה לרופא', '2026-04-11 17:00:00', NULL, 'fired')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_add(message_id=10, reaction='👍', chat_id='123')

    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM reminders WHERE message = 'לשלוח הודעה לרופא'").fetchone()[0]
    conn.close()
    assert status == 'dismissed'


def test_reaction_on_recurring_reminder_does_not_dismiss(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (11, 'outgoing', '⏰ לשתות מים')")
    conn.execute("INSERT INTO reminders (message, trigger_at, recurrence, status) VALUES ('לשתות מים', '2026-04-11 09:00:00', 'daily', 'pending')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_add(message_id=11, reaction='✅', chat_id='123')

    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM reminders WHERE message = 'לשתות מים'").fetchone()[0]
    conn.close()
    assert status == 'pending'  # unchanged


def test_unreact_on_reminder_reverts_to_pending(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text, reactions) VALUES (12, 'outgoing', '⏰ לשלוח הודעה לרופא', '[\"👍\"]')")
    conn.execute("INSERT INTO reminders (message, trigger_at, recurrence, status) VALUES ('לשלוח הודעה לרופא', '2026-04-11 17:00:00', NULL, 'dismissed')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_remove(message_id=12, reaction='👍', chat_id='123')

    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM reminders WHERE message = 'לשלוח הודעה לרופא'").fetchone()[0]
    conn.close()
    assert status == 'pending'  # reverts to pending so heartbeat can re-fire it


def test_reaction_on_task_nudge_marks_done(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (20, 'outgoing', '📋 [P7] לשלוח דוח שבועי — due in ~6h')")
    conn.execute("INSERT INTO tasks (title, priority, status) VALUES ('לשלוח דוח שבועי', 7, 'open')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_add(message_id=20, reaction='✅', chat_id='123')

    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM tasks WHERE title = 'לשלוח דוח שבועי'").fetchone()[0]
    conn.close()
    assert status == 'done'


def test_reaction_on_overdue_task_marks_done(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (21, 'outgoing', '⚠️ OVERDUE [P8]: לסיים פרויקט X')")
    conn.execute("INSERT INTO tasks (title, priority, status) VALUES ('לסיים פרויקט X', 8, 'open')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_add(message_id=21, reaction='👍', chat_id='123')

    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM tasks WHERE title = 'לסיים פרויקט X'").fetchone()[0]
    conn.close()
    assert status == 'done'


def test_unreact_on_task_reverts_to_open(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text, reactions) VALUES (22, 'outgoing', '📋 [P7] לשלוח דוח שבועי — due in ~6h', '[\"✅\"]')")
    conn.execute("INSERT INTO tasks (title, priority, status) VALUES ('לשלוח דוח שבועי', 7, 'done')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_remove(message_id=22, reaction='✅', chat_id='123')

    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM tasks WHERE title = 'לשלוח דוח שבועי'").fetchone()[0]
    conn.close()
    assert status == 'open'


def test_unreact_on_recurring_reminder_does_not_change_status(tmp_path):
    """Removing a reaction from a recurring reminder leaves status unchanged."""
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text, reactions) VALUES (13, 'outgoing', '⏰ לשתות מים', '[\"✅\"]')")
    conn.execute("INSERT INTO reminders (message, trigger_at, recurrence, status) VALUES ('לשתות מים', '2026-04-11 09:00:00', 'daily', 'pending')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_remove(message_id=13, reaction='✅', chat_id='123')

    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM reminders WHERE message = 'לשתות מים'").fetchone()[0]
    conn.close()
    assert status == 'pending'  # recurring reminders are never status-changed, even on remove


def test_reaction_on_unrecognized_message_only_logs(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (30, 'outgoing', 'שלום! איך אני יכול לעזור?')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_add(message_id=30, reaction='❤️', chat_id='123')

    conn = sqlite3.connect(db)
    reactions = conn.execute("SELECT reactions FROM messages WHERE telegram_message_id = 30").fetchone()[0]
    conn.close()
    assert json.loads(reactions) == ['❤️']  # logged, no error
