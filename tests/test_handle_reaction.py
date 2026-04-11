"""Tests for handle_reaction.py — run with: python -m pytest D:/Kai/tests/test_handle_reaction.py -v"""
import json
import os
import sqlite3
import sys
import tempfile

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
