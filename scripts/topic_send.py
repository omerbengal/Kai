#!/usr/bin/env python
"""Scheduled domain alerts go to the domain bot's forum topic, sent with THAT bot's token (06/11/2026,
Omer msgs 22614 / 22617): trading alerts -> 📈 (trading bot), registration-system alerts -> 🫡 (miluim bot).

Omer's reply to an alert, or a press on its buttons, then reaches the domain bot that owns the subject,
not main Kai. The row is saved in the bot's own bot.db (its history / context), never in kai.db.

Never loses an alert: if the bot has no token / no config, or the send fails, the alert goes to Omer's
private chat with main Kai's token, as before (and is saved in kai.db `messages` via tg_log).

    import topic_send
    mid, where = topic_send.send("trading", text, reply_to=None, keyboard=None)   # where = topic|private|None
"""
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import net_ipv4  # noqa: F401  IPv6 to api.telegram.org is slow on this PC
except Exception:
    pass
import kai_bots  # noqa: E402

OMER_CHAT = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
MAIN_DB = "D:/Kai/kai.db"


def main_token():
    import tg_silent_send
    return tg_silent_send.BOT_TOKEN


def bot_token(cfg):
    try:
        with open(os.path.join(cfg.get("state_dir", ""), ".env"), encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^TELEGRAM_BOT_TOKEN=(.*)$", line.strip())
                if m and m.group(1):
                    return m.group(1)
    except Exception:
        pass
    return None


def post(token, payload):
    """sendMessage; returns the message id or raises."""
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
    if not data.get("ok"):
        raise RuntimeError(str(data)[:200])
    return int(data["result"]["message_id"])


def _save_bot(bot, cfg, chat, thread, mid, text, reply_to):
    try:
        conn = sqlite3.connect(os.path.join(cfg["home"], "bot.db"), timeout=15)
        conn.execute("""CREATE TABLE IF NOT EXISTS bot_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, bot TEXT NOT NULL, chat_id TEXT, thread_id TEXT,
  telegram_message_id INTEGER, direction TEXT NOT NULL CHECK(direction IN ('incoming', 'outgoing')),
  text TEXT, reply_to_message_id INTEGER, ts DATETIME DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("INSERT INTO bot_messages (bot, chat_id, thread_id, telegram_message_id, direction, text,"
                     " reply_to_message_id, ts) VALUES (?,?,?,?,?,?,?,?)",
                     (bot, str(chat), str(thread), mid, "outgoing", text, int(reply_to) if reply_to else None,
                      datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _register_buttons(bot, chat, mid, text, keyboard, post_fn):
    """Open-buttons registry (07/11/2026): an answered question loses its buttons. Never raises."""
    if not keyboard or post_fn is not _REAL_POST:  # tests inject a fake post: no registry
        return
    try:
        import button_registry
        button_registry.register(chat, mid, text, keyboard, bot=bot, source="topic_send")
    except Exception as e:
        sys.stderr.write(f"[topic_send] button registry failed: {e}\n")


def _payload(chat, text, thread=None, reply_to=None, keyboard=None):
    p = {"chat_id": chat, "text": text}
    if thread:
        p["message_thread_id"] = int(thread)
    if reply_to:
        p["reply_parameters"] = {"message_id": int(reply_to), "allow_sending_without_reply": True}
    if keyboard:
        p["reply_markup"] = {"inline_keyboard": keyboard}
    return p


_REAL_POST = post


def send(domain, text, reply_to=None, keyboard=None, post=post, private_reply_to=None):
    """(message_id, 'topic' | 'private') or (None, None) if both sends failed.

    reply_to = a message in the TOPIC (e.g. the watcher's own earlier alert there). private_reply_to = a
    message in Omer's PRIVATE chat (e.g. his entry message for a position): used only by the private
    fallback, never in the topic, where that id is another message or none (swing/position watch, 06/11)."""
    cfg = (kai_bots.load_config() or {}).get(domain)
    token = bot_token(cfg) if cfg and cfg.get("group_id") and cfg.get("topics") else None
    if token:
        chat, thread = str(cfg["group_id"]), str(cfg["topics"][0])
        try:
            mid = post(token, _payload(chat, text, thread, reply_to, keyboard))
            _save_bot(domain, cfg, chat, thread, mid, text, reply_to)
            _register_buttons(domain, chat, mid, text, keyboard, post)
            return mid, "topic"
        except Exception as e:
            sys.stderr.write(f"[topic_send] {domain} topic send failed, falling back to private: {e}\n")
    try:
        # reply_to belongs to the topic's number space; never quote it in the private chat
        mid = post(main_token(), _payload(OMER_CHAT, text, reply_to=private_reply_to, keyboard=keyboard))
    except Exception as e:
        sys.stderr.write(f"[topic_send] private fallback failed: {e}\n")
        return None, None
    try:
        import tg_log
        tg_log.save_outgoing(mid, text)
    except Exception:
        pass
    _register_buttons("main", OMER_CHAT, mid, text, keyboard, post)
    return mid, "private"
