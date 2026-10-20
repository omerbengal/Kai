# -*- coding: utf-8 -*-
"""Tests for recurring-reminder nagging in heartbeat.py — see investigations/041.md.

Run with: python -m pytest D:/Kai/tests/test_recurring_nag.py -v
"""
import sqlite3
import sys

sys.path.insert(0, 'D:/Kai/scripts')

import heartbeat  # noqa: E402
import handle_reaction  # noqa: E402


def make_db(tmp_path):
    db_path = str(tmp_path / 'test.db')
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_message_id INTEGER,
            direction TEXT NOT NULL,
            text TEXT,
            reactions TEXT,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP,
            reminder_id INTEGER,
            task_id INTEGER
        );
        CREATE TABLE reminders (
            id INTEGER PRIMARY KEY,
            message TEXT NOT NULL,
            trigger_at DATETIME NOT NULL,
            recurrence TEXT,
            status TEXT DEFAULT 'pending',
            notification_message_id INTEGER,
            series_at DATETIME,
            nag_count INTEGER DEFAULT 0
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            priority INTEGER DEFAULT 5,
            status TEXT DEFAULT 'open',
            due_at DATETIME,
            completed_at DATETIME,
            notification_message_id INTEGER
        );
        CREATE TABLE task_notifications (
            id INTEGER PRIMARY KEY,
            task_id INTEGER,
            window TEXT,
            sent_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE message_reactions (
            id INTEGER PRIMARY KEY,
            telegram_message_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            emoji TEXT NOT NULL,
            action TEXT NOT NULL,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP
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


class Sender:
    """Stand-in for send_telegram that records instead of calling the Bot API."""

    def __init__(self):
        self.sent = []
        self._next_id = 90000

    def __call__(self, text, conn=None, **kwargs):  # alert / link / card_label (phone_links)
        self._next_id += 1
        self.sent.append((text, self._next_id))
        if conn is not None:
            conn.execute(
                "INSERT INTO messages (telegram_message_id, direction, text, ts) "
                "VALUES (?, 'outgoing', ?, datetime('now'))",
                (self._next_id, text)
            )
        return self._next_id


def setup(tmp_path, monkeypatch, recurrence='daily', nag_count=0):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO reminders (id, message, trigger_at, recurrence, status, nag_count) "
        "VALUES (1, 'לבדוק סדנה', datetime('now', '-1 minute'), ?, 'pending', ?)",
        (recurrence, nag_count)
    )
    conn.commit()
    conn.close()

    sender = Sender()
    monkeypatch.setattr(heartbeat, 'DB_PATH', db)
    monkeypatch.setattr(heartbeat, 'send_telegram', sender)
    monkeypatch.setattr(handle_reaction, 'DB_PATH', db)
    return db, sender


def state(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute(
        "SELECT trigger_at, series_at, nag_count, status, trigger_at > datetime('now') AS future "
        "FROM reminders WHERE id = 1"
    ).fetchone())
    conn.close()
    return row


def test_recurring_reminder_nags_instead_of_skipping_to_tomorrow(tmp_path, monkeypatch):
    db, sender = setup(tmp_path, monkeypatch)
    heartbeat.run_heartbeat()
    s = state(db)
    assert len(sender.sent) == 1
    assert s['nag_count'] == 1
    assert s['series_at'] is not None      # the occurrence being nagged about is pinned
    assert s['status'] == 'pending'
    # Re-armed in 5 minutes, NOT pushed a day out
    assert s['trigger_at'] < s['series_at'][:10] + ' 23:59:59'


def test_ack_advances_series_from_scheduled_time_not_from_nags(tmp_path, monkeypatch):
    db, sender = setup(tmp_path, monkeypatch)
    heartbeat.run_heartbeat()
    scheduled = state(db)['series_at']

    handle_reaction.handle_add(sender.sent[-1][1], '👍', '123')
    s = state(db)
    assert s['status'] == 'pending'        # the series survives the ack
    assert s['future'] == 1
    assert s['series_at'] is None and s['nag_count'] == 0
    # Next occurrence is exactly one day after the *scheduled* time — nags do not shift it
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT datetime(?, '-1 day')", (s['trigger_at'],)).fetchone()[0] == scheduled
    conn.close()

    heartbeat.run_heartbeat()
    assert len(sender.sent) == 1           # silent after the ack


def test_unreact_resumes_nagging(tmp_path, monkeypatch):
    db, sender = setup(tmp_path, monkeypatch)
    heartbeat.run_heartbeat()
    handle_reaction.handle_add(sender.sent[-1][1], '👍', '123')
    handle_reaction.handle_remove(sender.sent[-1][1], '👍', '123')
    assert state(db)['future'] == 0        # due again right away
    heartbeat.run_heartbeat()
    assert len(sender.sent) == 2


def test_nag_cap_rolls_forward_so_it_cannot_bleed_into_the_next_day(tmp_path, monkeypatch):
    db, sender = setup(tmp_path, monkeypatch, nag_count=heartbeat.MAX_RECURRING_NAGS - 1)
    heartbeat.run_heartbeat()
    s = state(db)
    assert s['nag_count'] == 0 and s['series_at'] is None
    assert s['future'] == 1


def test_one_shot_nagging_is_unchanged(tmp_path, monkeypatch):
    db, sender = setup(tmp_path, monkeypatch, recurrence=None)
    heartbeat.run_heartbeat()
    s = state(db)
    assert len(sender.sent) == 1
    assert s['status'] == 'pending' and s['future'] == 1
    assert s['series_at'] is None and s['nag_count'] == 0   # one-shots never use the series fields
