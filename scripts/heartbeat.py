#!/usr/bin/env python3
"""
Kai Heartbeat — standalone Python script for Windows Task Scheduler.
Runs every minute. Checks reminders, task nudges, overdue tasks.
Sends Telegram notifications and logs every run to the DB.
"""

import sqlite3
import urllib.request
import urllib.parse
import json
from datetime import datetime, timezone, timedelta

DB_PATH = r"D:\Kai\kai.db"
BOT_TOKEN = __import__("os").environ["KAI_TELEGRAM_BOT_TOKEN"]
CHAT_ID = __import__("os").environ.get("KAI_TELEGRAM_CHAT_ID", "")
JERUSALEM_TZ = timezone(timedelta(hours=3))  # UTC+3 (Israel DST)


def send_telegram(text: str, conn=None):
    """Send a Telegram message. If conn is provided, saves to messages table. Returns telegram message_id."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(url, payload, {"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read())
    tg_msg_id = data.get("result", {}).get("message_id")
    if conn is not None and tg_msg_id:
        try:
            conn.execute(
                "INSERT INTO messages (telegram_message_id, direction, text, ts) VALUES (?, 'outgoing', ?, datetime('now'))",
                (tg_msg_id, text)
            )
        except Exception:
            pass
    return tg_msg_id


def run_heartbeat():
    now_jerusalem = datetime.now(JERUSALEM_TZ)
    hour = now_jerusalem.hour

    # Sleep window: 01:00-05:59 only run on the hour
    if 1 <= hour <= 5 and now_jerusalem.minute != 0:
        return  # Silent exit

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    sent = []
    errors = []

    try:
        # Step 1: Due reminders
        cur.execute(
            "SELECT id, message, recurrence FROM reminders "
            "WHERE trigger_at <= datetime('now') AND status = 'pending'"
        )
        for rid, message, recurrence in cur.fetchall():
            try:
                send_telegram(f"⏰ {message}", conn=conn)
                sent.append(f"reminder:{rid}")
                if recurrence is None:
                    cur.execute("UPDATE reminders SET status = 'fired' WHERE id = ?", (rid,))
                elif recurrence == 'daily':
                    cur.execute("UPDATE reminders SET trigger_at = datetime(trigger_at, '+1 day') WHERE id = ?", (rid,))
                elif recurrence == 'weekly':
                    cur.execute("UPDATE reminders SET trigger_at = datetime(trigger_at, '+7 days') WHERE id = ?", (rid,))
                elif recurrence == 'monthly':
                    cur.execute("UPDATE reminders SET trigger_at = datetime(trigger_at, '+1 month') WHERE id = ?", (rid,))
                elif recurrence == 'yearly':
                    cur.execute("UPDATE reminders SET trigger_at = datetime(trigger_at, '+1 year') WHERE id = ?", (rid,))
                conn.commit()  # commit immediately so status is saved even if later steps fail
            except Exception as e:
                errors.append(f"reminder:{rid}:{e}")

        # Step 2: Task nudges (priority 5+, not overdue, with notification windows)
        cur.execute(
            "SELECT id, title, priority, due_at FROM tasks "
            "WHERE status = 'open' AND due_at IS NOT NULL "
            "AND due_at > datetime('now') AND priority >= 5"
        )
        tasks = cur.fetchall()

        windows_high = [('12h', 12, 6), ('6h', 6, 3), ('3h', 3, 1.5), ('1.5h', 1.5, 0)]
        windows_normal = [('24h', 24, 12), ('12h', 12, 6), ('6h', 6, 3), ('3h', 3, 1.5), ('1.5h', 1.5, 0)]

        for tid, title, priority, due_at in tasks:
            windows = windows_high if priority >= 10 else windows_normal
            for notif_type, hours_before, hours_after in windows:
                cur.execute(
                    "SELECT id FROM task_notifications WHERE task_id = ? AND notification_type = ?",
                    (tid, notif_type)
                )
                if cur.fetchone():
                    continue
                cur.execute(
                    "SELECT 1 WHERE datetime('now') BETWEEN "
                    "datetime(?, ? || ' hours') AND datetime(?, ? || ' hours')",
                    (due_at, f"-{hours_before}", due_at, f"-{hours_after}")
                )
                if cur.fetchone():
                    try:
                        send_telegram(f"📋 [P{priority}] {title} — due in ~{hours_before}h", conn=conn)
                        sent.append(f"task_nudge:{tid}:{notif_type}")
                        cur.execute(
                            "INSERT INTO task_notifications (task_id, notification_type) VALUES (?, ?)",
                            (tid, notif_type)
                        )
                        conn.commit()
                    except Exception as e:
                        errors.append(f"task:{tid}:{e}")

        # Step 3: Overdue tasks
        cur.execute(
            "SELECT id, title, priority FROM tasks "
            "WHERE status = 'open' AND due_at < datetime('now')"
        )
        for tid, title, priority in cur.fetchall():
            cur.execute(
                "SELECT id FROM task_notifications WHERE task_id = ? AND notification_type = 'overdue'",
                (tid,)
            )
            if not cur.fetchone():
                try:
                    send_telegram(f"⚠️ OVERDUE [P{priority}]: {title}", conn=conn)
                    sent.append(f"overdue:{tid}")
                    cur.execute(
                        "INSERT INTO task_notifications (task_id, notification_type) VALUES (?, ?)",
                        (tid, 'overdue')
                    )
                    conn.commit()
                except Exception as e:
                    errors.append(f"overdue:{tid}:{e}")

        # Step 4: Log every run
        details = "ok"
        if sent:
            details = "sent:" + ",".join(sent)
        if errors:
            details += " errors:" + ",".join(errors)

        cur.execute(
            "INSERT INTO action_log (agent, action, details, created_at) VALUES (?, ?, ?, datetime('now'))",
            ("heartbeat", "run", details)
        )
        conn.commit()

    finally:
        conn.close()


if __name__ == "__main__":
    run_heartbeat()
