"""⏰ reminders in the bots group's General (10/11/2026, Omer General msg 1616).

Group message ids collide with private ones, so every group row lives in chat_messages keyed by
(chat_id, telegram_message_id) and no group action may touch a private `messages` row.
Run: python -m pytest D:/Kai/tests/test_reminder_chat.py -v
"""
import json
import os
import sqlite3
import subprocess
import sys

import pytest

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(SCRIPTS, "fast"))

import reminder_chat  # noqa: E402
import heartbeat  # noqa: E402
import handle_reaction  # noqa: E402
import phone_links  # noqa: E402

G = reminder_chat.GENERAL_CHAT
P = reminder_chat.PRIVATE_CHAT


def make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_message_id INTEGER,
            direction TEXT, text TEXT, reply_to_message_id INTEGER, reply_to_message_text TEXT,
            reactions TEXT, ts TEXT, reminder_id INTEGER, task_id INTEGER);
        CREATE TABLE reminders (id INTEGER PRIMARY KEY, message TEXT, trigger_at TEXT, recurrence TEXT,
            status TEXT DEFAULT 'pending', series_at TEXT, nag_count INTEGER DEFAULT 0, card_label TEXT);
        CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT, priority INTEGER, status TEXT DEFAULT 'open',
            completed_at TEXT);
        CREATE TABLE message_reactions (id INTEGER PRIMARY KEY, telegram_message_id INTEGER, direction TEXT,
            emoji TEXT, action TEXT, ts TEXT);
        CREATE TABLE action_log (id INTEGER PRIMARY KEY, agent TEXT, action TEXT, details TEXT, created_at TEXT);
        CREATE TABLE ack_log (id INTEGER PRIMARY KEY, ack_msg_id INTEGER, action TEXT, text TEXT, ts TEXT);
    """)
    conn.commit()
    return conn


def real_schema_db(path):
    """Empty copy of kai.db's schema (run_heartbeat touches many tables)."""
    src = sqlite3.connect(os.path.join(os.path.dirname(SCRIPTS), "kai.db"))
    ddl = [r[0] for r in src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL "
                                      "AND name NOT LIKE 'sqlite_%'")]
    src.close()
    conn = sqlite3.connect(path)
    for d in ddl:
        conn.execute(d)
    conn.commit()
    return conn


# ---- the switch -----------------------------------------------------------------------------

def test_switch_round_trip(tmp_path):
    cfg = str(tmp_path / "reminder_chat.json")
    assert reminder_chat.target(cfg) == "private"          # missing file = the old behaviour
    reminder_chat.set_target("general", cfg)
    assert reminder_chat.reminder_chat_id(cfg) == G
    reminder_chat.set_target("private", cfg)
    assert reminder_chat.reminder_chat_id(cfg) == P
    with open(cfg, "w") as f:
        f.write("{broken")
    assert reminder_chat.reminder_chat_id(cfg) == P        # broken file never blocks reminders
    with pytest.raises(ValueError):
        reminder_chat.set_target("somewhere", cfg)


def test_is_group():
    assert reminder_chat.is_group(G) and reminder_chat.is_group(int(G))
    assert not reminder_chat.is_group(P) and not reminder_chat.is_group(None)


# ---- heartbeat send ----------------------------------------------------------------------------

def fake_api(calls, mid=1000):
    state = {"id": mid}

    def api(method, payload, timeout=10):
        calls.append((method, dict(payload)))
        state["id"] += 1
        return {"ok": True, "result": {"message_id": state["id"]}}
    return api


def test_group_send_goes_to_chat_messages_only(tmp_path, monkeypatch):
    conn = make_db(str(tmp_path / "t.db"))
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (1001, 'outgoing', 'private')")
    calls = []
    monkeypatch.setattr(heartbeat, "_tg_api", fake_api(calls))
    monkeypatch.setattr(phone_links, "_load_config", lambda: {"contact_mode": "card"})
    mid = heartbeat.send_telegram("⏰ לקנות חלב", conn=conn, alert=True, link=("reminder_id", 7), chat_id=G)
    assert mid == 1001
    assert calls[0][1]["chat_id"] == G and "message_thread_id" not in calls[0][1]
    # the private row with the SAME id is untouched and NOT linked to the reminder
    assert conn.execute("SELECT reminder_id FROM messages WHERE telegram_message_id=1001").fetchone() == (None,)
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert reminder_chat.reminder_for_message(conn, G, 1001) == 7
    assert reminder_chat.reminder_for_message(conn, P, 1001) is None


def test_group_contact_card_is_linked_in_group(tmp_path, monkeypatch):
    conn = make_db(str(tmp_path / "t.db"))
    calls = []
    monkeypatch.setattr(heartbeat, "_tg_api", fake_api(calls))
    monkeypatch.setattr(phone_links, "flood_until", lambda now=None: None)
    monkeypatch.setattr(phone_links, "_load_config", lambda: {"contact_mode": "card"})
    mid = heartbeat.send_telegram("⏰ להתקשר למוביט - *5090", conn=conn, alert=True,
                                  link=("reminder_id", 9), chat_id=G)
    assert calls[0][0] == "sendContact" and calls[0][1]["chat_id"] == G
    assert reminder_chat.reminder_for_message(conn, G, mid) == 9
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_default_send_stays_private(tmp_path, monkeypatch):
    conn = make_db(str(tmp_path / "t.db"))
    calls = []
    monkeypatch.setattr(heartbeat, "_tg_api", fake_api(calls))
    monkeypatch.setattr(phone_links, "_load_config", lambda: {"contact_mode": "card"})
    mid = heartbeat.send_telegram("⏰ x", conn=conn, alert=True, link=("reminder_id", 3))
    assert calls[0][1]["chat_id"] == P
    assert conn.execute("SELECT reminder_id FROM messages WHERE telegram_message_id=?", (mid,)).fetchone() == (3,)


def test_heartbeat_fires_to_general_and_renags(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    conn = real_schema_db(db)
    conn.execute("INSERT INTO reminders (id, message, trigger_at) VALUES (5, 'בדיקה', datetime('now','-1 minute'))")
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (1001, 'outgoing', 'private')")
    conn.commit(); conn.close()
    cfg = str(tmp_path / "rc.json"); reminder_chat.set_target("general", cfg)
    monkeypatch.setattr(reminder_chat, "CONFIG_PATH", cfg)
    monkeypatch.setattr(heartbeat, "DB_PATH", db)
    monkeypatch.setattr(phone_links, "_load_config", lambda: {"contact_mode": "card"})
    monkeypatch.setattr(heartbeat, "local_now", lambda: __import__("datetime").datetime(2026, 11, 10, 12, 0))
    calls = []
    monkeypatch.setattr(heartbeat, "_tg_api", fake_api(calls))
    heartbeat.run_heartbeat()
    conn = sqlite3.connect(db)
    sends = [c for c in calls if c[0] == "sendMessage" and c[1]["text"].startswith("⏰")]
    assert sends and sends[0][1]["chat_id"] == G
    assert reminder_chat.reminder_for_message(conn, G, 1001) == 5
    assert conn.execute("SELECT reminder_id FROM messages WHERE telegram_message_id=1001").fetchone() == (None,)
    st, nxt = conn.execute("SELECT status, trigger_at > datetime('now') FROM reminders WHERE id=5").fetchone()
    assert st == "pending" and nxt == 1                     # the 5-minute re-nag is armed


def test_general_failure_falls_back_to_private(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    conn = real_schema_db(db)
    conn.execute("INSERT INTO reminders (id, message, trigger_at) VALUES (6, 'גיבוי', datetime('now','-1 minute'))")
    conn.commit(); conn.close()
    cfg = str(tmp_path / "rc.json"); reminder_chat.set_target("general", cfg)
    monkeypatch.setattr(reminder_chat, "CONFIG_PATH", cfg)
    monkeypatch.setattr(heartbeat, "DB_PATH", db)
    monkeypatch.setattr(phone_links, "_load_config", lambda: {"contact_mode": "card"})
    monkeypatch.setattr(heartbeat, "local_now", lambda: __import__("datetime").datetime(2026, 11, 10, 12, 0))
    calls = []
    ok = fake_api(calls)

    def api(method, payload, timeout=10):
        if payload.get("chat_id") == G:
            raise OSError("group down")
        return ok(method, payload, timeout)
    monkeypatch.setattr(heartbeat, "_tg_api", api)
    heartbeat.run_heartbeat()
    conn = sqlite3.connect(db)
    assert any(c[1]["chat_id"] == P and c[1]["text"].startswith("⏰") for c in calls)
    assert conn.execute("SELECT COUNT(*) FROM action_log WHERE action='reminder_chat_fallback'").fetchone()[0] == 1


# ---- 👍 in General -------------------------------------------------------------------------------

def _reaction_db(tmp_path, monkeypatch):
    db = str(tmp_path / "r.db")
    conn = make_db(db)
    conn.execute("INSERT INTO reminders (id, message, trigger_at) VALUES (1, 'פרטית', datetime('now'))")
    conn.execute("INSERT INTO reminders (id, message, trigger_at) VALUES (2, 'בקבוצה', datetime('now'))")
    # private ⏰ with id 500 linked to reminder 1, General ⏰ with the SAME id linked to reminder 2
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text, reminder_id) "
                 "VALUES (500, 'outgoing', '⏰ פרטית', 1)")
    reminder_chat.save_row(conn, G, 500, "⏰ בקבוצה", link=("reminder_id", 2))
    conn.commit(); conn.close()
    monkeypatch.setattr(handle_reaction, "DB_PATH", db)
    return db


def test_thumbs_up_in_general_dismisses_only_the_group_reminder(tmp_path, monkeypatch):
    db = _reaction_db(tmp_path, monkeypatch)
    handle_reaction.handle_add(message_id=500, reaction="👍", chat_id=G)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT status FROM reminders WHERE id=2").fetchone()[0] == "dismissed"
    assert conn.execute("SELECT status FROM reminders WHERE id=1").fetchone()[0] == "pending"
    assert conn.execute("SELECT reactions FROM messages WHERE telegram_message_id=500").fetchone()[0] is None
    handle_reaction.handle_remove(message_id=500, reaction="👍", chat_id=G)
    assert sqlite3.connect(db).execute("SELECT status FROM reminders WHERE id=2").fetchone()[0] == "pending"


def test_thumbs_up_in_private_still_uses_messages(tmp_path, monkeypatch):
    db = _reaction_db(tmp_path, monkeypatch)
    handle_reaction.handle_add(message_id=500, reaction="👍", chat_id=P)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT status FROM reminders WHERE id=1").fetchone()[0] == "dismissed"
    assert conn.execute("SELECT status FROM reminders WHERE id=2").fetchone()[0] == "pending"


def test_general_reaction_on_unknown_message_never_falls_back_to_private(tmp_path, monkeypatch):
    db = _reaction_db(tmp_path, monkeypatch)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text, reminder_id) "
                 "VALUES (777, 'outgoing', '⏰ פרטית', 1)")
    conn.commit(); conn.close()
    with pytest.raises(handle_reaction.MessageNotFound):
        handle_reaction.handle_add(message_id=777, reaction="👍", chat_id=G)
    assert sqlite3.connect(db).execute("SELECT status FROM reminders WHERE id=1").fetchone()[0] == "pending"


# ---- fast scripts in General -------------------------------------------------------------------

def test_base_group_save_and_delete_never_touch_private_rows(tmp_path, monkeypatch):
    import _base
    db = str(tmp_path / "b.db")
    conn = make_db(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (900, 'outgoing', 'private')")
    conn.commit(); conn.close()
    monkeypatch.setattr(_base, "DB_PATH", db)

    class Resp:
        def read(self):
            return json.dumps({"ok": True, "result": {"message_id": 900}}).encode()
    monkeypatch.setattr(_base.urllib.request, "urlopen", lambda *a, **k: Resp())
    assert _base.tg_reply(G, "‏✅ תזכורת עודכנה", reply_to=1) == 900
    _base.tg_delete(G, 900)
    _base.log_ack  # noqa: B018
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE telegram_message_id=900").fetchone()[0] == 1
    reminder_chat.ensure_table(conn)
    assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0  # saved, then deleted


def test_reply_reschedule_in_general_picks_the_exact_reminder(tmp_path):
    db = str(tmp_path / "u.db")
    conn = make_db(db)
    conn.execute("INSERT INTO reminders (id, message, trigger_at, status) VALUES (1, 'לקנות חלב', '2026-11-10 08:00:00', 'pending')")
    conn.execute("INSERT INTO reminders (id, message, trigger_at, status) VALUES (2, 'לקנות חלב', '2026-11-10 09:00:00', 'dismissed')")
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text, reminder_id) VALUES (321, 'outgoing', '⏰ x', 1)")
    reminder_chat.save_row(conn, G, 321, "⏰ לקנות חלב", link=("reminder_id", 2))
    conn.commit(); conn.close()
    code = f"""
import sys, sqlite3
sys.path.insert(0, r'{SCRIPTS}'); sys.path.insert(0, r'{os.path.join(SCRIPTS, "fast")}')
import _base
_base.DB_PATH = r'{db}'
sent = []
_base.tg_reply = lambda chat, text, reply_to=None, linkify=False: sent.append((chat, text)) or 1
_base.tg_delete = lambda chat, mid: None
_base.unpin_ack = lambda *a, **k: None
_base.log_ack = lambda *a, **k: None
_base.action_log = lambda *a, **k: None
import fast_reminder_update as m
m.get_db = lambda: (lambda c: (setattr(c, 'row_factory', sqlite3.Row), c)[1])(sqlite3.connect(r'{db}'))
for n in ('tg_reply', 'tg_delete', 'action_log', 'unpin_ack', 'log_ack'):
    setattr(m, n, getattr(_base, n))
sys.argv = ['x', '--reply_msg_id', '321', '--trigger_at', '2026-11-11 10:00:00', '--ack_id', '5',
            '--orig_id', '6', '--chat_id', '{G}']
m.main()
print(sent[0][0])
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == G
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT trigger_at, status FROM reminders WHERE id=2").fetchone() == ("2026-11-11 10:00:00", "pending")
    assert conn.execute("SELECT trigger_at, status FROM reminders WHERE id=1").fetchone() == ("2026-11-10 08:00:00", "pending")


# ---- Option D fast scripts allowed from General (10/11/2026, Omer General msg 1656) -------------
# The dispatcher runs these with --chat_id <General>. Each must: reply in General, delete the General
# ACK, save its reply only to chat_messages, and never touch a private `messages` / ack_log row.

GENERAL_FAST = [
    ("fast_reminder_create", ["--message", "להתקשר", "--trigger_at", "2030-01-01 10:00:00"]),
    ("fast_reminder_list", ["--days", "30"]),
    ("fast_reminder_update", ["--message", "לקנות חלב", "--trigger_at", "2030-01-02 10:00:00"]),
    ("fast_reminder_cancel", ["--message", "לקנות חלב"]),
    ("fast_reminder_delete", ["--message", "לקנות חלב"]),
    ("fast_task_create", ["--title", "משימת בדיקה", "--priority", "5"]),
    ("fast_tasks_list", ["--filter", "all"]),
    ("fast_task_done", ["--title", "לתקן אופניים"]),
    ("fast_task_update", ["--title", "לתקן אופניים", "--priority", "7"]),
    ("fast_task_delete", ["--title", "לתקן אופניים"]),
]


@pytest.mark.parametrize("script,extra", GENERAL_FAST, ids=[s for s, _ in GENERAL_FAST])
def test_fast_script_from_general_stays_in_general(tmp_path, script, extra):
    db = str(tmp_path / "g.db")
    conn = real_schema_db(db)
    conn.execute("INSERT INTO reminders (message, trigger_at, status) VALUES ('לקנות חלב', '2030-01-01 08:00:00', 'pending')")
    conn.execute("INSERT INTO tasks (title, priority, status) VALUES ('לתקן אופניים', 5, 'open')")
    # private rows that share the ids used below: must survive untouched
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (5, 'outgoing', 'private ack')")
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (777, 'outgoing', 'private reply')")
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    ack_before = conn.execute("SELECT COUNT(*) FROM ack_log").fetchone()[0]
    conn.close()
    calls_file = str(tmp_path / "calls.json")
    code = f"""
import sys, json, urllib.request
sys.path.insert(0, r'{SCRIPTS}'); sys.path.insert(0, r'{os.path.join(SCRIPTS, "fast")}')
calls = []
class R:
    def __init__(self, body): self.b = body
    def read(self): return self.b
    def __enter__(self): return self
    def __exit__(self, *a): return False
def fake(req, *a, **k):
    url = req if isinstance(req, str) else req.full_url
    data = None if isinstance(req, str) else req.data
    calls.append({{'url': url.split('/bot', 1)[-1].split('/', 1)[-1], 'data': data.decode('utf-8') if data else None}})
    return R(json.dumps({{'ok': True, 'result': {{'message_id': 777}}}}).encode())
urllib.request.urlopen = fake
import _base
_base.DB_PATH = r'{db}'
_base.unpin_ack = lambda *a, **k: None
_base.action_log = lambda *a, **k: None
import {script} as m
m.unpin_ack = _base.unpin_ack
m.action_log = _base.action_log
sys.argv = ['x'] + {extra!r} + ['--ack_id', '5', '--orig_id', '6', '--chat_id', '{G}']
try:
    m.main()
finally:
    json.dump(calls, open(r'{calls_file}', 'w', encoding='utf-8'), ensure_ascii=False)
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                         cwd=str(tmp_path))
    assert out.returncode == 0, out.stderr
    assert "fail:" not in out.stderr, out.stderr
    calls = json.load(open(calls_file, encoding="utf-8"))
    sends = [c for c in calls if c["url"].startswith("sendMessage")]
    deletes = [c for c in calls if c["url"].startswith("deleteMessage")]
    assert sends, calls
    for c in sends:
        assert json.loads(c["data"])["chat_id"] == G
        assert "fast-script error" not in c["data"]
    assert any(f"chat_id={G}" in c["url"] and "message_id=5" in c["url"] for c in deletes), deletes
    assert not any(f"chat_id={P}" in c["url"] or f'"chat_id": "{P}"' in (c["data"] or "") for c in calls)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == before
    assert conn.execute("SELECT text FROM messages WHERE telegram_message_id=5").fetchone()[0] == "private ack"
    assert conn.execute("SELECT COUNT(*) FROM ack_log").fetchone()[0] == ack_before
    reminder_chat.ensure_table(conn)
    assert conn.execute("SELECT COUNT(*) FROM chat_messages WHERE chat_id=? AND telegram_message_id=777",
                        (G,)).fetchone()[0] == 1
