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
BOT_TOKEN = os.environ["KAI_TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:  # IPv4 first: IPv6 to api.telegram.org hangs ~80% of the time on this PC (net_ipv4.py).
    import net_ipv4  # noqa: F401  Without it a sendContact hit the 10s timeout (20/10 11:20 UTC)
except Exception:
    pass
try:
    import local_tz  # home timezone: Israel, or the destination while travel mode is on
except Exception:  # never let the tz helper stop reminders from firing
    local_tz = None
JERUSALEM_TZ = timezone(timedelta(hours=3))  # legacy fallback only - use local_now()


def local_now():
    if local_tz is not None:
        try:
            return local_tz.now_local()
        except Exception:
            pass
    return datetime.now(JERUSALEM_TZ)


def _tg_api(method, payload, timeout=10):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def _err_text(e):
    """Readable reason for a failed Bot API call (HTTPError body holds Telegram's description)."""
    try:
        body = e.read().decode("utf-8", "replace")  # urllib.error.HTTPError
        return f"{type(e).__name__} {getattr(e, 'code', '')}: {body[:200]}"
    except Exception:
        return f"{type(e).__name__}: {str(e)[:200]}"


def _log_card_issue(conn, details):
    """action_log row for every contact-card fallback / retry (agent 'heartbeat', action 'contact_card')."""
    if conn is None:
        return
    try:
        conn.execute("INSERT INTO action_log (agent, action, details) VALUES ('heartbeat', 'contact_card', ?)",
                     (details,))
    except Exception:
        pass


def _save_row(conn, tg_msg_id, text, link=None, reply_to=None):
    if conn is None or not tg_msg_id:
        return
    try:
        conn.execute(
            "INSERT INTO messages (telegram_message_id, direction, text, reply_to_message_id, ts) "
            "VALUES (?, 'outgoing', ?, ?, datetime('now'))",
            (tg_msg_id, text, reply_to)
        )
        if link:  # reminder_id / task_id: a 👍 on this message (or its contact card) acts on it
            col, val = link
            if col in ("reminder_id", "task_id"):
                conn.execute(f"UPDATE messages SET {col} = ? WHERE telegram_message_id = ?", (val, tg_msg_id))
    except Exception:
        pass


def send_telegram(text: str, conn=None, alert=False, link=None, card_label=None):
    """Send a Telegram message. If conn is provided, saves to messages table. Returns telegram message_id.

    Phone numbers (scripts/phone_links.py, config/phone_links.json): mobile numbers are rewritten
    to +972-5X-XXX-XXXX (Telegram links them). A star code / landline in a single alert (alert=True)
    turns the whole alert into a contact card ("contact_mode": "card") whose name is the alert text
    (or `card_label`, a short label stored with the reminder); in "below" mode the card replies to
    the text message. Every card row is saved with the same `link` (reminder_id / task_id) so a 👍
    on it marks the reminder done. Any card failure falls back to the plain text message.
    """
    plan = None
    try:
        import phone_links
        plan = phone_links.plan(text, alert=alert)
        if plan["mode"] == "card" and card_label:
            prefix = text.split(" ", 1)[0] + " "
            first, last = phone_links.card_names(prefix + card_label)
            plan["cards"][0].update(first=first, last=last)
    except Exception as e:
        plan = None
        _log_card_issue(conn, f"phone_links plan failed, plain text: {_err_text(e)}")

    if plan and plan["mode"] == "card":
        c = plan["cards"][0]
        reasons = []
        for attempt in (1, 2):  # one retry: a single slow/failed call must not turn the card into text
            try:
                data = _tg_api("sendContact", phone_links.contact_payload(CHAT_ID, c["number"], c["first"], c["last"]),
                               timeout=15)
                tg_msg_id = data.get("result", {}).get("message_id")
                if tg_msg_id:
                    _save_row(conn, tg_msg_id, text, link)
                    if reasons:
                        _log_card_issue(conn, f"card sent on retry after: {reasons[0]} | {text[:60]}")
                    return tg_msg_id
                reasons.append(f"no message_id: {str(data)[:150]}")
            except Exception as e:
                reasons.append(_err_text(e))
                if "retry after" in reasons[-1].lower():
                    phone_links.note_flood(reasons[-1])  # no sendContact at all until it ends
                    break  # flood limit: retrying now fails again, send the text
        # never lose a reminder over a card - send the text instead, and say why in action_log
        _log_card_issue(conn, f"card failed, sent as text: {' / '.join(reasons)} | {text[:60]}")
        plan = {"mode": "text", "text": plan["text"], "cards": []}

    base_payload = {"chat_id": CHAT_ID, "text": text}
    payload = base_payload
    if plan and plan["mode"] == "text":
        payload = {"chat_id": CHAT_ID, "text": plan["text"]}
    elif plan and plan["mode"] == "legacy":  # dormant Mini App / dial-page path
        try:
            payload = phone_links.telegram_payload(base_payload)
        except Exception:
            payload = base_payload

    try:
        data = _tg_api("sendMessage", payload)
    except Exception:
        if payload is base_payload:
            raise
        data = _tg_api("sendMessage", base_payload)  # never lose a reminder over a link
    tg_msg_id = data.get("result", {}).get("message_id")
    _save_row(conn, tg_msg_id, text, link)
    if tg_msg_id and plan and plan["mode"] == "text":
        for c in plan["cards"]:
            try:
                r = _tg_api("sendContact", phone_links.contact_payload(
                    CHAT_ID, c["number"], c["first"], c["last"], reply_to=tg_msg_id, silent=True))
                cid = r.get("result", {}).get("message_id")
                _save_row(conn, cid, f"[כרטיס חיוג להודעה {tg_msg_id}] {text}", link, reply_to=tg_msg_id)
            except Exception as e:
                why = _err_text(e)
                phone_links.note_flood(why)
                _log_card_issue(conn, f"card below {tg_msg_id} failed: {why}")
    return tg_msg_id


# A recurring reminder nags every 5 min until acknowledged, but gives up after this many
# nags (24 x 5 min = 2h) and rolls forward to the next occurrence, so an unacknowledged
# daily reminder cannot nag all the way into the following day.
MAX_RECURRING_NAGS = 24

RECURRENCE_STEP = {
    'daily': '+1 day',
    'weekly': '+7 days',
    'monthly': '+1 month',
    'yearly': '+1 year',
}


def advance_recurring(cur, rid, recurrence, series_at):
    """Move a recurring reminder to its next occurrence after series_at and clear the nag state.

    Steps forward repeatedly so a stale series_at (e.g. a reminder that was paused or
    manually rescheduled) still lands on a future time instead of firing instantly.
    """
    step = RECURRENCE_STEP.get(recurrence)
    if step is None:
        return
    next_at = series_at
    for _ in range(400):
        next_at = cur.execute("SELECT datetime(?, ?)", (next_at, step)).fetchone()[0]
        if cur.execute("SELECT ? > datetime('now')", (next_at,)).fetchone()[0]:
            break
    cur.execute(
        "UPDATE reminders SET trigger_at = ?, series_at = NULL, nag_count = 0, status = 'pending' "
        "WHERE id = ?",
        (next_at, rid)
    )


def run_heartbeat():
    # Travel mode auto-return after the end date (sends its own Telegram message once).
    try:
        import travel_mode
        travel_mode.expire_check()
    except Exception:
        pass

    now_jerusalem = local_now()  # name kept; this is LOCAL time (see local_tz.py)
    hour = now_jerusalem.hour

    # Sleep window: 01:00-05:59 local, only run on the hour
    if 1 <= hour <= 5 and now_jerusalem.minute != 0:
        return  # Silent exit

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:  # reminders.card_label (20/10/2026): short contact-card name, see phone_links.py
        if "card_label" not in [r[1] for r in cur.execute("PRAGMA table_info(reminders)")]:
            cur.execute("ALTER TABLE reminders ADD COLUMN card_label TEXT")
            conn.commit()
    except Exception:
        pass

    sent = []
    errors = []

    try:
        # Step 1: Due reminders
        cur.execute(
            "SELECT id, message, recurrence, trigger_at, series_at, COALESCE(nag_count, 0), card_label FROM reminders "
            "WHERE trigger_at <= datetime('now') AND status = 'pending'"
        )
        for rid, message, recurrence, trigger_at, series_at, nag_count, card_label in cur.fetchall():
            try:
                tg_msg_id = send_telegram(f"⏰ {message}", conn=conn, alert=True,
                                          link=("reminder_id", rid), card_label=card_label)
                if tg_msg_id:
                    conn.execute(
                        "UPDATE messages SET reminder_id = ? WHERE telegram_message_id = ?",
                        (rid, tg_msg_id)
                    )
                sent.append(f"reminder:{rid}")
                if recurrence is None:
                    # Re-nag: reset to pending in 5 minutes from original trigger_at (not 'now')
                    # Using trigger_at as base avoids sub-second drift turning 5min into 6min
                    cur.execute(
                        "UPDATE reminders SET status = 'pending', trigger_at = datetime(trigger_at, '+5 minutes') WHERE id = ?",
                        (rid,)
                    )
                else:
                    # Recurring reminders nag too (see investigation #041). series_at pins the
                    # occurrence that is currently being nagged about, so the next occurrence is
                    # always computed from the scheduled time and never drifts by the nag offsets.
                    if series_at is None:
                        series_at = trigger_at
                        cur.execute("UPDATE reminders SET series_at = ? WHERE id = ?", (series_at, rid))
                    nag_count += 1
                    if nag_count < MAX_RECURRING_NAGS:
                        cur.execute(
                            "UPDATE reminders SET trigger_at = datetime(trigger_at, '+5 minutes'), "
                            "nag_count = ? WHERE id = ?",
                            (nag_count, rid)
                        )
                    else:
                        # Nag cap reached without an ack — give up on this occurrence, move to next
                        advance_recurring(cur, rid, recurrence, series_at)
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
                        tg_msg_id = send_telegram(f"📋 [P{priority}] {title} — due in ~{hours_before}h", conn=conn,
                                                  alert=True, link=("task_id", tid))
                        if tg_msg_id:
                            conn.execute(
                                "UPDATE messages SET task_id = ? WHERE telegram_message_id = ?",
                                (tid, tg_msg_id)
                            )
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
                    tg_msg_id = send_telegram(f"⚠️ OVERDUE [P{priority}]: {title}", conn=conn,
                                              alert=True, link=("task_id", tid))
                    if tg_msg_id:
                        conn.execute(
                            "UPDATE messages SET task_id = ? WHERE telegram_message_id = ?",
                            (tid, tg_msg_id)
                        )
                    sent.append(f"overdue:{tid}")
                    cur.execute(
                        "INSERT INTO task_notifications (task_id, notification_type) VALUES (?, ?)",
                        (tid, 'overdue')
                    )
                    conn.commit()
                except Exception as e:
                    errors.append(f"overdue:{tid}:{e}")

        # Step 5: JSONL context size warning — alert before auto-compact hits
        try:
            import os, glob as glob_mod
            jsonl_dir = r"C:/Users/Omer/.claude/projects/D--Kai"
            jsonl_files = [f for f in glob_mod.glob(os.path.join(jsonl_dir, "*.jsonl"))]
            if jsonl_files:
                current_jsonl = max(jsonl_files, key=os.path.getmtime)
                file_size = os.path.getsize(current_jsonl)

                # Read last 3MB to find the most recent compact_boundary marker
                WINDOW = 3 * 1024 * 1024
                read_size = min(WINDOW, file_size)
                with open(current_jsonl, 'rb') as f:
                    f.seek(file_size - read_size)
                    tail = f.read()

                last_boundary = tail.rfind(b'compact_boundary')
                if last_boundary >= 0:
                    line_end = tail.find(b'\n', last_boundary)
                    if line_end < 0:
                        line_end = len(tail)
                    bytes_since = len(tail) - line_end
                    # Absolute byte position of this compact_boundary in the file
                    boundary_abs_pos = (file_size - read_size) + last_boundary
                else:
                    # No boundary found → entire window counts as "since compact"
                    bytes_since = read_size
                    boundary_abs_pos = -1  # fresh session, no compact yet

                MB = bytes_since / (1024 * 1024)

                level = None
                if MB >= 1.1:
                    level = 'critical'
                elif MB >= 0.9:
                    level = 'warning'

                if level:
                    # De-dup: one alert per compact cycle (identified by boundary byte position)
                    cur.execute("""
                        SELECT id FROM action_log
                        WHERE agent = 'heartbeat' AND action = 'context_size_alert'
                        AND details LIKE ?
                        LIMIT 1
                    """, (f"boundary_pos:{boundary_abs_pos}%",))
                    if not cur.fetchone():
                        emoji = '🔴' if level == 'critical' else '🟡'
                        msg = f"{emoji} קונטקסט: {MB:.2f}MB מאז ה-compact האחרון"
                        if level == 'critical':
                            msg += " — compact עלול לקרות בקרוב!"
                        send_telegram(msg, conn=conn)
                        sent.append(f"context_size_alert:{level}:{MB:.2f}MB")
                        cur.execute(
                            "INSERT INTO action_log (agent, action, details, created_at) VALUES (?, ?, ?, datetime('now'))",
                            ("heartbeat", "context_size_alert", f"boundary_pos:{boundary_abs_pos},level:{level},mb:{MB:.2f}")
                        )
                        conn.commit()
        except Exception as e:
            errors.append(f"context_check:{e}")

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
    try:
        import stale_ack
        stale_ack.fix_stale()
    except Exception as e:  # a cosmetic safety net must never fail the heartbeat
        print(f"stale_ack error: {e}")
