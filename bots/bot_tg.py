#!/usr/bin/env python
"""Telegram + history helpers for a Kai domain bot (multi-bot stage 1, 05/11/2026).

A domain bot NEVER uses the main Kai scripts that send with the main token
(tg_silent_send, dispatch_start, tg_pin, fast/*, log_ack...): they would post as
main Kai into the private chat. Everything a domain bot needs is here, with its
own token from <state_dir>/.env.

History lives in kai.db table bot_messages (bot, chat_id, thread_id, ...), NOT in
`messages`: group message ids are a separate number space, and the main scripts
delete `messages` rows by telegram_message_id alone.

History lives in the bot's OWN db file <home>/bot.db (separation step 1, 05/11/2026,
docs/multi_bot_separation_design.md); kai.db keeps no bot rows (`migrate` moved them).

CLI:
  bot_tg.py context --bot miluim [--n 20]            last N messages of this bot (local time)
  bot_tg.py auto-ack --bot miluim                    UserPromptSubmit hook: silent "🔄 ⏳" in the topic
  bot_tg.py send --bot miluim --chat C [--thread T] [--reply-to M] --text "..."   (handoff posts)
  bot_tg.py migrate --bot miluim                     move this bot's rows kai.db -> <home>/bot.db
  bot_tg.py save --bot miluim --mode incoming|outgoing   (hooks; JSON on stdin)
  bot_tg.py delete --bot miluim --chat C --msg M      delete a message (the ACK) + its row
  bot_tg.py pin|unpin --bot miluim --chat C --msg M   silent pin / unpin
"""
import io
import json
import os
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "D:/Kai/scripts")
try:
    import net_ipv4  # noqa: F401  IPv6 to api.telegram.org is slow on this PC
except Exception:
    pass
import kai_bots  # noqa: E402

KAI_DB = "D:/Kai/kai.db"   # only for `migrate` (the old shared home of bot_messages)
DB_PATH = None              # None = the bot's own <home>/bot.db

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot TEXT NOT NULL,
  chat_id TEXT,
  thread_id TEXT,
  telegram_message_id INTEGER,
  direction TEXT NOT NULL CHECK(direction IN ('incoming', 'outgoing')),
  text TEXT,
  reply_to_message_id INTEGER,
  ts DATETIME DEFAULT CURRENT_TIMESTAMP
)"""


def db_for(bot):
    return os.path.join(kai_bots.bot(bot)["home"], "bot.db")


def _conn(db_path=DB_PATH, bot=None):
    conn = sqlite3.connect(db_path or db_for(bot))
    conn.text_factory = str
    conn.execute(SCHEMA)
    return conn


def token(name: str) -> str:
    env = os.path.join(kai_bots.bot(name)["state_dir"], ".env")
    with open(env, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^TELEGRAM_BOT_TOKEN=(.*)$", line.strip())
            if m:
                return m.group(1)
    raise SystemExit(f"no TELEGRAM_BOT_TOKEN in {env}")


def api(name: str, method: str, **params):
    url = f"https://api.telegram.org/bot{token(name)}/{method}"
    data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
    with urllib.request.urlopen(url, data=data, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def save(bot, direction, chat_id, thread_id, msg_id, text, reply_to=None, ts=None, db_path=DB_PATH):
    conn = _conn(db_path, bot)
    conn.execute(
        "INSERT INTO bot_messages (bot, chat_id, thread_id, telegram_message_id, direction, text,"
        " reply_to_message_id, ts) VALUES (?,?,?,?,?,?,?,?)",
        (bot, chat_id, thread_id, msg_id, direction, text, reply_to,
         ts or datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def parse_channel(prompt: str):
    m = re.search(r"<channel([\s\S]*?)>([\s\S]*?)</channel>", prompt or "")
    if not m:
        return None
    attrs, text = m.group(1), m.group(2).strip()

    def attr(n):
        a = re.search(rf'\b{n}="(.*?)"', attrs, re.DOTALL)
        return a.group(1) if a else None
    return {"chat_id": attr("chat_id"), "message_id": attr("message_id"),
            "thread_id": attr("message_thread_id"), "reply_to": attr("reply_to_message_id"),
            "ts": attr("ts"), "text": text}


def sent_ids(response: str):
    m = re.search(r"sent \(id: (\d+)\)", response)
    if m:
        return [int(m.group(1))]
    m = re.search(r"sent \d+ parts \(ids: ([\d,\s]+)\)", response)
    return [int(x) for x in re.findall(r"\d+", m.group(1))] if m else []


def hook_save(bot, mode, data, db_path=DB_PATH):
    if mode == "incoming":
        c = parse_channel(data.get("prompt", "") or data.get("content", ""))
        if not c or not c["message_id"]:
            return
        save(bot, "incoming", c["chat_id"], c["thread_id"], int(c["message_id"]), c["text"],
             int(c["reply_to"]) if c["reply_to"] else None, c["ts"], db_path)
    elif mode == "outgoing":
        ti = data.get("tool_input", {}) or {}
        ids = sent_ids(json.dumps(data.get("tool_response", {}))) or [None]
        for i, mid in enumerate(ids):
            save(bot, "outgoing", ti.get("chat_id"), ti.get("message_thread_id"), mid,
                 ti.get("text", "") if i == 0 else f"[part {i + 1}]", None, None, db_path)


def context(bot, n=20, db_path=DB_PATH):
    sys.path.insert(0, "D:/Kai/scripts")
    try:
        import local_tz
        fmt = local_tz.fmt_local
    except Exception:
        fmt = str
    conn = _conn(db_path, bot)
    rows = conn.execute(
        "SELECT ts, direction, thread_id, telegram_message_id, text FROM bot_messages"
        " WHERE bot=? ORDER BY id DESC LIMIT ?", (bot, n)).fetchall()
    conn.close()
    out = []
    for ts, d, tid, mid, text in reversed(rows):
        try:
            when = fmt(ts)
        except Exception:
            when = ts
        out.append(f"[{when}] {d} topic={tid} id={mid}: {(text or '')[:400]}")
    return "\n".join(out)


def migrate(bot, src=KAI_DB):
    """Move this bot's rows from the shared kai.db into <home>/bot.db (idempotent)."""
    s = sqlite3.connect(src)
    s.text_factory = str
    have = s.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bot_messages'").fetchone()
    rows = s.execute(
        "SELECT chat_id, thread_id, telegram_message_id, direction, text, reply_to_message_id, ts"
        " FROM bot_messages WHERE bot=? ORDER BY id", (bot,)).fetchall() if have else []
    d = _conn(bot=bot)
    for r in rows:
        d.execute("INSERT INTO bot_messages (bot, chat_id, thread_id, telegram_message_id, direction, text,"
                  " reply_to_message_id, ts) VALUES (?,?,?,?,?,?,?,?)", (bot, *r))
    d.commit()
    total = d.execute("SELECT COUNT(*) FROM bot_messages WHERE bot=?", (bot,)).fetchone()[0]
    d.close()
    if rows:
        s.execute("DELETE FROM bot_messages WHERE bot=?", (bot,))
        s.commit()
    s.close()
    return len(rows), total


def send(bot, chat, text, thread=None, reply_to=None, silent=False, timeout=15):
    """Plain sendMessage with this bot's token (handoff posts, auto-ACK). Saves the outgoing row."""
    payload = {"chat_id": chat, "text": text}
    if thread:
        payload["message_thread_id"] = int(thread)
    if reply_to:
        payload["reply_parameters"] = {"message_id": int(reply_to), "allow_sending_without_reply": True}
    if silent:
        payload["disable_notification"] = True
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token(bot)}/sendMessage",
        data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(str(data)[:200])
    mid = int(data["result"]["message_id"])
    try:
        save(bot, "outgoing", str(chat), str(thread) if thread else None, mid, text,
             int(reply_to) if reply_to else None)
    except Exception:
        pass
    return mid


TEXT_ACK = "🔄 ⏳"
VOICE_ACK = "🎤 מתמלל... ⏳"
OMER_DM = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")


def ack_classify(bot, attrs, content):
    """('none', reason) or ('ack', text). Same rules as main Kai's scripts/auto_ack.py, but the allowed
    chats are this bot's group (+ Omer's DM with this bot)."""
    import auto_ack as main_ack  # D:/Kai/scripts: reuse the trivial list, never its sender
    cfg = kai_bots.bot(bot)
    if attrs.get("event_type") == "reaction" or re.match(r"^\[reaction on message \d+\]", content):
        return ("none", "reaction")
    if attrs.get("chat_id") not in {str(cfg.get("group_id")), OMER_DM}:
        return ("none", "other_chat")
    if not attrs.get("message_id"):
        return ("none", "no_message_id")
    if attrs.get("attachment_kind") in {"voice", "audio"}:
        return ("ack", VOICE_ACK)
    if "callback_data" in attrs or content.startswith("[callback:"):
        return ("ack", TEXT_ACK)
    if not attrs.get("attachment_kind") and not attrs.get("image_path") and main_ack.is_trivial(content):
        return ("none", "trivial")
    return ("ack", TEXT_ACK)


def auto_ack(bot, prompt, dry_run=False, sender=None):
    import auto_ack as main_ack
    sender = sender or send
    reg = os.path.join(kai_bots.bot(bot)["home"], "tmp", "auto_ack")
    lines = []
    for attrs, content in main_ack.parse_channels(prompt):
        mid = attrs.get("message_id", "?")
        kind, val = ack_classify(bot, attrs, content)
        if kind == "none":
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID=NONE reason={val}")
            continue
        if dry_run:
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID=DRYRUN text={val}")
            continue
        os.makedirs(reg, exist_ok=True)
        key = os.path.join(reg, main_ack.registry_key(attrs) + ".ack")
        try:
            fd = os.open(key, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except FileExistsError:
            try:
                prev = open(key, encoding="utf-8").read().strip()
            except Exception:
                prev = ""
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID={prev or 'FAILED'} (already sent)")
            continue
        try:
            ack = sender(bot, attrs.get("chat_id"), val, thread=attrs.get("message_thread_id"),
                         reply_to=mid, silent=True, timeout=3)
            with open(key, "w", encoding="utf-8") as f:
                f.write(str(ack))
            _typing("attach_ack", bot, mid, ack)
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID={ack}")
        except Exception as e:
            try:
                os.remove(key)
            except Exception:
                pass
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID=FAILED ({type(e).__name__})")
    return lines


def _typing(fn, bot, msg_id, ack_id):
    """Tell bots/bot_typing.py about an ACK (attach on send, end the task on delete). Never raises."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import bot_typing
        p = bot_typing.Paths(bot)
        if fn == "attach_ack":
            bot_typing.attach_ack(p, msg_id, ack_id)
        else:
            bot_typing.end_task(p, ack_id=ack_id)
    except Exception:
        pass


def _arg(argv, flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv else default


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    bot = _arg(argv, "--bot")
    if cmd == "save":
        try:
            data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
            hook_save(bot, _arg(argv, "--mode"), data)
        except Exception as e:  # a hook must never break the session
            print(f"[bot_tg] save failed: {e}", file=sys.stderr)
        return 0
    if cmd == "auto-ack":
        try:
            data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
            lines = auto_ack(bot, data.get("prompt", "") or data.get("content", ""), "--dry-run" in argv)
        except Exception:
            return 0  # never block the prompt
        if lines:
            print("<system-reminder>\n" + "\n".join(lines) +
                  "\n(bot auto-ACK hook: the ACK is already in the topic. Use AUTO_ACK_MSG_ID as ACK_MSG_ID"
                  " (edit it, delete it at the end with bot_tg.py delete); do NOT send another ACK."
                  " NONE = no ACK was sent; FAILED = send it yourself as before)\n</system-reminder>")
        return 0
    if cmd == "send":
        print(send(bot, _arg(argv, "--chat"), _arg(argv, "--text"), _arg(argv, "--thread"),
                   _arg(argv, "--reply-to")))
        return 0
    if cmd == "migrate":
        moved, total = migrate(bot)
        print(f"moved {moved} rows; {bot} bot.db now has {total}")
        return 0
    if cmd == "context":
        print(context(bot, int(_arg(argv, "--n", 20))))
        return 0
    if cmd in ("delete", "pin", "unpin"):
        chat, msg = _arg(argv, "--chat"), _arg(argv, "--msg")
        method = {"delete": "deleteMessage", "pin": "pinChatMessage", "unpin": "unpinChatMessage"}[cmd]
        extra = {"disable_notification": "true"} if cmd == "pin" else {}
        try:
            print(json.dumps(api(bot, method, chat_id=chat, message_id=msg, **extra)))
        except Exception as e:
            print(f"[bot_tg] {method} failed: {e}")
        if cmd == "delete":
            _typing("end_task", bot, None, msg)
            conn = _conn(bot=bot)
            conn.execute("DELETE FROM bot_messages WHERE bot=? AND chat_id=? AND telegram_message_id=?",
                         (bot, chat, int(msg)))
            conn.commit()
            conn.close()
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.exit(main(sys.argv))
