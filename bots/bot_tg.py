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
  bot_tg.py react --bot miluim --chat C --msg M --emoji 👀   set (replace) this bot's reaction
  bot_tg.py general-done --bot miluim --chat C --tag-msg M --summary "..." [--fail] [--detail-msg D]
                                                     end of a General task: 👍/👎 on the tag + ONE short line
                                                     in General (reply to the tag) + link to the details
  bot_tg.py ask --bot miluim --to whatsapp --text "..." [--origin-tag N] [--wait 420]
                                                     ask another bot by tagging it in General; waits for its
                                                     reply there and prints it (JSON). Bot-to-bot, 06/11/2026
  bot_tg.py ask-wait --bot miluim --id <request msg> [--timeout 420]   keep waiting for that answer
  bot_tg.py general-wait --bot miluim --chat C --tag-msg M [--question-msg Q]
                                                     stopped to ask Omer: 🤔 on the tag, no General line; his
                                                     answer in the topic reopens it (👀, GENERAL_PENDING_TAG)

General tasks (stage 3, 06/11/2026, docs/multi_bot_stage3_main_dispatcher.md s.2): a message in the
group's General topic (no message_thread_id) reaches a bot only when it tags the bot (main Kai's handoff,
or Omer) or replies to it. The auto-ACK hook then sets 👀 on it and puts the ACK in the bot's OWN topic;
all the work happens there; `general-done` closes it.
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


def _conn(db_path=DB_PATH, bot=None, timeout=5.0):
    conn = sqlite3.connect(db_path or db_for(bot), timeout=timeout)
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


def save(bot, direction, chat_id, thread_id, msg_id, text, reply_to=None, ts=None, db_path=DB_PATH,
         db_timeout=5.0):
    conn = _conn(db_path, bot, timeout=db_timeout)
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
        if db_path is None:  # tests pass their own db: no registry, no Telegram calls
            _buttons("on_outgoing", ti, json.dumps(data.get("tool_response", {})), bot)
    if mode == "incoming" and db_path is None:
        _buttons("on_incoming", data.get("prompt", "") or data.get("content", ""), bot)


def _buttons(fn, *args):
    """Open-buttons registry (scripts/button_registry.py, 07/11/2026): this bot's questions with buttons
    live in its own bot.db; a press / an answer removes the buttons + adds the "✅ נבחר" line."""
    try:
        import button_registry
        getattr(button_registry, fn)(*args)
    except Exception as e:
        print(f"[bot_tg] buttons {fn} failed: {e}", file=sys.stderr)


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
             int(reply_to) if reply_to else None, db_timeout=min(timeout, 5))
    except Exception:
        pass
    return mid


TEXT_ACK = "🔄 ⏳"
HOOK_NET_TIMEOUT = 2   # per network call inside the auto-ACK hook (its hard limit is 8 s, 06/11/2026)
SEEN = "👀"       # ReactionTypeEmoji is a fixed list (checked 06/11/2026): ✅ and skin tones are NOT on it
DONE_OK = "👍"
DONE_FAIL = "👎"
WAITING = "🤔"    # waiting for Omer's answer in the topic (on the fixed reaction list)
REOPEN_HOURS = 24
MAX_SUMMARY_WORDS = 15
MAX_SUMMARY_CHARS = 120
VOICE_ACK = "🎤 מתמלל... ⏳"
OMER_DM = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
OMER_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
MAIN_BOT_ID = "<REDACTED_SECRET>"   # main Kai: its General tags are handoffs (stage 3)
# Bot-to-bot requests through General (06/11/2026, Omer msgs 358 / 361, "חלוקת תפקידים טהורה")
ASK_WINDOW_S = 600
ASK_MAX_PER_WINDOW = 5          # outgoing asks per bot per window
BOT_TAGS_MAX_PER_WINDOW = 8     # incoming tags from bots per window, all bots together
ASK_DEFAULT_WAIT = 420          # one Bash call is capped at 10 min
DEPTH_HOURS = 1                 # a bot tag still open this recently blocks asking (depth 1)
BOT_ANSWER_MAX_LINES = 10
BOT_ANSWER_MAX_CHARS = 700


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
    uid = attrs.get("user_id")
    if uid and uid not in {OMER_ID, MAIN_BOT_ID} and not bot_name_by_id(uid):
        return ("none", "stranger")   # the plugin's allowFrom should already drop it; belt and braces
    if attrs.get("attachment_kind") in {"voice", "audio"}:
        return ("ack", VOICE_ACK)
    if "callback_data" in attrs or content.startswith("[callback:"):
        return ("ack", TEXT_ACK)
    if not attrs.get("attachment_kind") and not attrs.get("image_path") and main_ack.is_trivial(content):
        return ("none", "trivial")
    return ("ack", TEXT_ACK)


def own_topic(bot):
    topics = kai_bots.bot(bot).get("topics") or []
    return str(topics[0]) if topics else None


def is_general(bot, attrs):
    """A message in the group's General topic (no thread id) = a tag of / reply to this bot there."""
    return (attrs.get("chat_id") == str(kai_bots.bot(bot).get("group_id"))
            and not attrs.get("message_thread_id") and attrs.get("event_type") != "reaction")


def react(bot, chat, msg, emoji, timeout=10):
    """setMessageReaction with this bot's token; a new call replaces this bot's previous reaction."""
    payload = {"chat_id": chat, "message_id": int(msg), "reaction": [{"type": "emoji", "emoji": emoji}]}
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token(bot)}/setMessageReaction",
        data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(str(data)[:200])
    return True


def topic_link(group_id, thread, msg):
    g = str(group_id)
    internal = g[4:] if g.startswith("-100") else g.lstrip("-")
    return f"https://t.me/c/{internal}/{thread}/{msg}"


def general_done(bot, chat, tag_msg, summary, ok=True, detail_msg=None, sender=None, reactor=None,
                 deleter=None):
    """End of a General task: 👍 / 👎 on the tag message + ONE short line in General (reply to the tag).
    Omer 06/11: "הודעת הסיכום צריכה להיות ממש קצרה" - a long or multi-line summary is refused here."""
    sender, reactor = sender or send, reactor or react
    s = (summary or "").strip().lstrip("‏").strip()
    tag_info = load_tags(bot).get(str(tag_msg), {})
    if tag_info.get("from_bot"):
        # a tag from another of our bots (bot_tg.py ask): this General reply IS the answer that bot waits for
        # (its hook captures it), so it may carry the data, still short (06/11/2026, Omer msgs 358 / 361)
        lines_ = [ln.strip().lstrip("‏").strip() for ln in s.splitlines() if ln.strip()]
        if not lines_ or len(lines_) > BOT_ANSWER_MAX_LINES or len(s) > BOT_ANSWER_MAX_CHARS:
            raise ValueError(f"an answer to a bot is <= {BOT_ANSWER_MAX_LINES} lines / {BOT_ANSWER_MAX_CHARS}"
                             " chars: only what the asking bot needs")
        s = "\n‏".join(lines_)
    elif not s or "\n" in s or len(s.split()) > MAX_SUMMARY_WORDS or len(s) > MAX_SUMMARY_CHARS:
        raise ValueError(f"summary must be ONE line, <= {MAX_SUMMARY_WORDS} words / {MAX_SUMMARY_CHARS} chars"
                         " (the details go in the topic)")
    if tag_info.get("state") == "waiting":
        raise ValueError("this tag is WAITING for Omer's answer (general-wait): not done yet. Close it only"
                         " after his answer, when the work is really over")
    try:
        reactor(bot, chat, tag_msg, DONE_OK if ok else DONE_FAIL)
    except Exception:
        pass
    try:
        _set_tag(bot, tag_msg, chat=chat, state="done", detail=str(detail_msg) if detail_msg else None)
    except Exception:
        pass
    close_tag_ack(bot, chat, tag_msg, deleter)
    text = "‏" + s
    if detail_msg:
        text += "\n‏" + topic_link(kai_bots.bot(bot).get("group_id"), own_topic(bot), detail_msg)
    return sender(bot, chat, text, thread=None, reply_to=tag_msg)


# --- General tag states (06/11/2026, Omer msg 22669: 👍 showed while the real work only started) ----------
# <home>/tmp/general_tags.json  {tag_msg: {chat, state: working|waiting|done, ts, question?, detail?,
# close_on_ack?}}. A question = general-wait (🤔); Omer's answer in the topic reopens the tag (👀) and the
# auto-ACK line carries GENERAL_PENDING_TAG so the follow-up finish closes the ORIGINAL tag.

def _tags_path(bot):
    return os.path.join(kai_bots.bot(bot)["home"], "tmp", "general_tags.json")


def load_tags(bot):
    try:
        with open(_tags_path(bot), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_tags(bot, tags):
    p = _tags_path(bot)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    cutoff = datetime.now(timezone.utc).timestamp() - 7 * 86400
    tags = {k: v for k, v in tags.items() if v.get("ts", cutoff + 1) > cutoff}
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tags, f, ensure_ascii=False)
    os.replace(tmp, p)


def _set_tag(bot, tag, **fields):
    tags = load_tags(bot)
    t = tags.get(str(tag), {})
    t.update(fields)
    t["ts"] = datetime.now(timezone.utc).timestamp()
    tags[str(tag)] = t
    save_tags(bot, tags)
    return t


def _pin(cmd, bot, chat, msg):
    """pin / unpin a bot ACK through scripts/tg_pin.py with this bot's token. Never raises."""
    try:
        sys.path.insert(0, "D:/Kai/scripts")
        import tg_pin
        (tg_pin.pin_ack if cmd == "pin" else tg_pin.unpin_ack)(msg, chat, bot=bot)
    except Exception:
        pass


def drop_ack(bot, chat, ack_id):
    """Delete a bot ACK: unpin + deleteMessage + end its typing task + drop its bot_messages row (= the `delete` CLI)."""
    _pin("unpin", bot, chat, ack_id)
    try:
        api(bot, "deleteMessage", chat_id=chat, message_id=ack_id)
    finally:
        _typing("end_task", bot, None, ack_id)
        try:
            conn = _conn(bot=bot)
            conn.execute("DELETE FROM bot_messages WHERE bot=? AND chat_id=? AND telegram_message_id=?",
                         (bot, str(chat), int(ack_id)))
            conn.commit()
            conn.close()
        except Exception:
            pass


def close_tag_ack(bot, chat, tag_msg, deleter=None):
    """The ACK the hook sent for a General tag is deleted when the tag closes or waits (06/11/2026, Omer
    msg 273: a tag closed with a plain `react 👍` left the ACK in the topic). Never raises."""
    try:
        ack = load_tags(bot).get(str(tag_msg), {}).get("ack")
        if not ack:
            return False
        try:
            (deleter or drop_ack)(bot, chat, ack)
        except Exception:
            pass
        _set_tag(bot, tag_msg, ack=None)
        return True
    except Exception:
        return False


def log_react_failure(bot, chat, msg, emoji, err):
    """A failed reaction used to vanish silently (whatsapp 👀 on tag 236, 06/11/2026). Never raises."""
    try:
        p = os.path.join(kai_bots.bot(bot)["home"], "tmp", "react_failures.log")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {bot} chat={chat} msg={msg} {emoji} {err}\n")
    except Exception:
        pass


def seen_with_retry(bot, chat, msg, reactor):
    """👀 on a General tag: one retry after a failure, each failure logged; never blocks the ACK."""
    for attempt in (1, 2):
        try:
            reactor(bot, chat, msg, SEEN)
            return True
        except Exception as e:
            log_react_failure(bot, chat, msg, SEEN, f"attempt={attempt} {e}")
    return False


def react_tag(bot, chat, msg, emoji, reactor=None, deleter=None):
    """`react` CLI: a 👍 / 👎 on a tracked General tag also closes it in general_tags.json (a trivial tag
    closed by a plain react stayed "working", 06/11/2026). A waiting tag (🤔) refuses, like general-done."""
    reactor = reactor or react
    t = load_tags(bot).get(str(msg))
    closing = emoji in (DONE_OK, DONE_FAIL)
    if t and closing and t.get("state") == "waiting":
        raise ValueError(f"tag {msg} is waiting for Omer's answer: no 👍 yet")
    reactor(bot, chat, msg, emoji)
    if t and closing:
        _set_tag(bot, msg, state="done")
        close_tag_ack(bot, chat, msg, deleter)
    return True


def general_wait(bot, chat, tag_msg, question_msg=None, reactor=None, deleter=None):
    """The bot stopped to ask Omer (question / proposal / buttons): 🤔 on the tag, NO line in General."""
    reactor = reactor or react
    try:
        reactor(bot, chat, tag_msg, WAITING)
    except Exception:
        pass
    _set_tag(bot, tag_msg, chat=chat, state="waiting", question=str(question_msg) if question_msg else None)
    close_tag_ack(bot, chat, tag_msg, deleter)
    return "waiting"


def reopen_tag(bot, attrs, reactor=None):
    """Omer's answer in the bot's own topic: the General tag it answers goes back to 👀 (working).
    Match: a callback on / reply to the question or the done detail message; else the newest waiting tag."""
    reactor = reactor or react
    if attrs.get("chat_id") != str(kai_bots.bot(bot).get("group_id")) or not attrs.get("message_thread_id"):
        return None
    target = attrs.get("message_id") if "callback_data" in attrs else attrs.get("reply_to_message_id")
    now = datetime.now(timezone.utc).timestamp()
    tags = {k: v for k, v in load_tags(bot).items() if now - v.get("ts", 0) < REOPEN_HOURS * 3600}
    hit = None
    if target:
        for k, v in tags.items():
            if (v.get("state") == "waiting" and v.get("question") == str(target)) or \
               (v.get("state") == "done" and v.get("detail") == str(target)):
                hit = k
    if hit is None:
        waiting = sorted((v.get("ts", 0), k) for k, v in tags.items() if v.get("state") == "waiting")
        hit = waiting[-1][1] if waiting else None
    if hit is None:
        return None
    try:
        reactor(bot, tags[hit].get("chat"), hit, SEEN)
    except Exception:
        pass
    _set_tag(bot, hit, state="working")
    return hit


def close_on_ack_delete(bot, ack_id, reactor=None):
    """One-off safety net: a tag marked close_on_ack=<ACK> gets 👍 when that ACK is deleted."""
    reactor = reactor or react
    for k, v in load_tags(bot).items():
        if v.get("close_on_ack") == str(ack_id) and v.get("state") == "working":
            try:
                reactor(bot, v.get("chat"), k, DONE_OK)
            except Exception:
                pass
            _set_tag(bot, k, state="done", close_on_ack=None)


# --- Bot-to-bot requests through General (06/11/2026, Omer msgs 358 / 361) --------------------------------
# A bot that needs another bot's domain (WhatsApp: only the WhatsApp bot) tags it in General with `ask`.
# Everything is a visible Telegram message; nothing goes through a hidden channel. The target handles it like
# main Kai's tag (👀, its own topic, general-done = a reply to the tag in General). The requester's auto-ACK
# hook catches that reply (BOT_ANSWER) and stores it in <home>/tmp/bot_requests.json, where the waiting
# `ask` / `ask-wait` reads it. Loop guards: not self, not main Kai, depth 1, rate limits, chatter ignored.

def all_bots():
    return kai_bots.load_config()


def bot_name_by_id(uid):
    if not uid:
        return None
    try:
        for n, c in all_bots().items():
            if str(c.get("bot_id") or "") == str(uid):
                return n
    except Exception:
        pass
    return None


def _req_path(bot):
    return os.path.join(kai_bots.bot(bot)["home"], "tmp", "bot_requests.json")


def _waiter_path(bot, rid):
    return os.path.join(kai_bots.bot(bot)["home"], "tmp", "bot_request_waiters", f"{rid}.until")


def load_requests(bot):
    try:
        with open(_req_path(bot), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_requests(bot, reqs):
    p = _req_path(bot)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    cutoff = datetime.now(timezone.utc).timestamp() - 7 * 86400
    reqs = {k: v for k, v in reqs.items() if v.get("ts", cutoff + 1) > cutoff}
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reqs, f, ensure_ascii=False)
    os.replace(tmp, p)


def _update_request(bot, rid, **fields):
    reqs = load_requests(bot)
    r = reqs.get(str(rid), {})
    r.update(fields)
    reqs[str(rid)] = r
    _save_requests(bot, reqs)
    return r


def ask(bot, to, text, origin_tag=None, sender=None, reactor=None, wait=ASK_DEFAULT_WAIT, sleep=None,
        clock=None):
    """Tag bot `to` in General with a request; returns its answer (or status waiting after `wait` s)."""
    import time as _t
    sender, reactor = sender or send, reactor or react
    clock = clock or _t.time
    allb = all_bots()
    if to == bot:
        raise ValueError("a bot never tags itself")
    if to not in allb:
        raise ValueError(f"unknown bot '{to}' (main Kai is not asked through General; tell Omer instead)")
    uname = allb[to].get("username")
    if not uname:
        raise ValueError(f"{to} has no username in config/bots.json")
    text = (text or "").strip()
    if not text:
        raise ValueError("empty request")
    now = clock()
    for k, v in load_tags(bot).items():
        if v.get("from_bot") and v.get("state") in ("working", "waiting") and \
                now - v.get("ts", 0) < DEPTH_HOURS * 3600:
            raise ValueError(f"depth 1: you are answering a request from {v['from_bot']} (tag {k}); a bot never"
                             " tags another bot while answering a bot. Answer with what you have.")
    recent = [v for v in load_requests(bot).values() if now - v.get("ts", 0) < ASK_WINDOW_S]
    if len(recent) >= ASK_MAX_PER_WINDOW:
        raise ValueError(f"rate limit: {ASK_MAX_PER_WINDOW} requests to other bots in {ASK_WINDOW_S // 60} min")
    chat = kai_bots.bot(bot).get("group_id")
    mid = sender(bot, chat, f"@{uname} {text}", thread=None, reply_to=None, silent=False)
    _update_request(bot, mid, to=to, text=text, chat=str(chat), ts=now, state="waiting",
                    origin_tag=str(origin_tag) if origin_tag else None, answer=None, answer_msg=None, details=[])
    if origin_tag:
        try:
            reactor(bot, chat, origin_tag, WAITING)
        except Exception:
            pass
    if not wait:
        return {"request_msg": mid, "to": to, "status": "waiting"}
    return ask_wait(bot, mid, timeout=wait, sleep=sleep, clock=clock)


def ask_wait(bot, rid, timeout=ASK_DEFAULT_WAIT, sleep=None, clock=None):
    import time as _t
    sleep, clock = sleep or _t.sleep, clock or _t.time
    rid = str(rid)
    start = clock()
    wp = _waiter_path(bot, rid)
    try:
        os.makedirs(os.path.dirname(wp), exist_ok=True)
        with open(wp, "w", encoding="utf-8") as f:
            f.write(str(datetime.now(timezone.utc).timestamp() + float(timeout) + 5))
    except Exception:
        pass
    while True:
        r = load_requests(bot).get(rid)
        if not r:
            return {"request_msg": rid, "status": "unknown", "note": "no such request of this bot"}
        if r.get("state") == "answered":
            return {"request_msg": rid, "to": r.get("to"), "status": "answered", "answer": r.get("answer"),
                    "answer_msg": r.get("answer_msg"), "details": [d.get("text") for d in r.get("details", [])]}
        if clock() - start >= float(timeout):
            return {"request_msg": rid, "to": r.get("to"), "status": "waiting",
                    "note": f"no answer yet. Continue with `bot_tg.py ask-wait --bot {bot} --id {rid}`. A WhatsApp"
                            " send waits for Omer's button in the WhatsApp bot's topic; never ask again"}
        sleep(2)


def _waiter_live(bot, rid):
    try:
        with open(_waiter_path(bot, rid), encoding="utf-8") as f:
            return datetime.now(timezone.utc).timestamp() < float(f.read().strip())
    except Exception:
        return False


def bot_inbound(bot, from_bot, attrs, content, reactor=None, dry_run=False):
    """A message from ANOTHER of our bots. Returns the hook line, or None when it is a real tag of this bot
    (then the normal General-tag path runs, with FROM_BOT)."""
    reactor = reactor or _hook_react
    mid = attrs.get("message_id", "?")
    head = f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID=NONE"
    rt = attrs.get("reply_to_message_id")
    req = load_requests(bot).get(str(rt)) if rt else None
    if req and req.get("to") == from_bot:
        body = (content or "").strip()
        if body in (TEXT_ACK, VOICE_ACK) or body.startswith("🔄"):
            return f"{head} reason=bot_ack (the other bot's ACK: nothing to do)"
        if attrs.get("message_thread_id"):
            if not dry_run:
                _update_request(bot, rt, details=req.get("details", []) + [{"msg": mid, "text": body[:3000]}])
            return f"{head} reason=bot_detail BOT_ANSWER_DETAIL request={rt} from={from_bot} (stored; do nothing)"
        live = _waiter_live(bot, rt)
        if not dry_run:
            _update_request(bot, rt, state="answered", answer=body[:3000], answer_msg=mid)
            if req.get("origin_tag"):
                try:
                    reactor(bot, req.get("chat") or attrs.get("chat_id"), req["origin_tag"], SEEN)
                except Exception:
                    pass
        hint = ("the waiting `ask` has it: do NOTHING, no reply, no ACK" if live else
                "nobody waits for it any more: if it finishes an open task of yours, finish that task in your"
                " topic (never reply to the other bot); otherwise do nothing")
        return (f"{head} reason=bot_answer BOT_ANSWER request={rt} from={from_bot}"
                f" waiter={'live' if live else 'none'} ({hint})")
    me = (kai_bots.bot(bot).get("username") or "").lower()
    if not attrs.get("message_thread_id") and me and f"@{me}" in (content or "").lower():
        now = datetime.now(timezone.utc).timestamp()
        recent = [v for v in load_tags(bot).values() if v.get("from_bot") and now - v.get("ts", 0) < ASK_WINDOW_S]
        if len(recent) >= BOT_TAGS_MAX_PER_WINDOW:
            return f"{head} reason=bot_rate_limit BOT_CHATTER from={from_bot} (too many bot requests: ignore it)"
        return None
    return (f"{head} reason=bot_chatter BOT_CHATTER from={from_bot} (not a tag of you and not an answer to"
            " you: ignore it, never reply)")


def _hook_react(bot, chat, msg, emoji):
    return react(bot, chat, msg, emoji, timeout=HOOK_NET_TIMEOUT)


def _timing(bot, msg, t0, ack):
    """One line per hook run: the hook is killed at 8 s and then leaves no other trace. Never raises."""
    import time as _t
    try:
        p = os.path.join(kai_bots.bot(bot)["home"], "tmp", "auto_ack_timing.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} msg={msg} ack={ack} {(_t.time() - t0):.2f}s\n")
    except Exception:
        pass


def auto_ack(bot, prompt, dry_run=False, sender=None, reactor=None):
    import auto_ack as main_ack
    import time as _t
    sender, reactor = sender or send, reactor or _hook_react
    t0 = _t.time()
    reg = os.path.join(kai_bots.bot(bot)["home"], "tmp", "auto_ack")
    lines = []
    for attrs, content in main_ack.parse_channels(prompt):
        mid = attrs.get("message_id", "?")
        kind, val = ack_classify(bot, attrs, content)
        if kind == "none" and val == "stranger":
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID=NONE reason=stranger (ignore it, no reply)")
            continue
        from_bot = bot_name_by_id(attrs.get("user_id"))
        if from_bot == bot:
            from_bot = None
        if from_bot:
            try:
                bline = bot_inbound(bot, from_bot, attrs, content, reactor=reactor, dry_run=dry_run)
            except Exception as e:
                bline = (f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID=NONE reason=bot_error ({type(e).__name__})"
                         " BOT_CHATTER (ignore it)")
            if bline is not None:
                lines.append(bline)
                continue
        gen = mid != "?" and is_general(bot, attrs)
        tag = f" GENERAL_TAG=1 TAG_MSG_ID={mid} WORK_THREAD_ID={own_topic(bot)}" if gen else ""
        if gen and from_bot:
            tag += f" FROM_BOT={from_bot}"
        if gen and not dry_run:
            seen_with_retry(bot, attrs.get("chat_id"), mid, reactor)
            if kind != "none":
                try:
                    _set_tag(bot, mid, chat=attrs.get("chat_id"), state="working",
                             **({"from_bot": from_bot} if from_bot else {}))
                except Exception:
                    pass
        elif not dry_run and mid != "?" and kind != "none":
            try:
                pending = reopen_tag(bot, attrs, reactor=reactor)
            except Exception:
                pending = None
            if pending:
                tag = f" GENERAL_PENDING_TAG={pending}"
        if kind == "none":
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID=NONE reason={val}{tag}")
            continue
        if dry_run:
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID=DRYRUN text={val}{tag}")
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
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID={prev or 'FAILED'} (already sent){tag}")
            continue
        try:
            if gen:  # the work happens in the bot's own topic, as a reply quoting the General tag (Omer 06/11)
                ack = sender(bot, attrs.get("chat_id"), val, thread=own_topic(bot),
                             reply_to=mid, silent=True, timeout=3)
            else:
                ack = sender(bot, attrs.get("chat_id"), val, thread=attrs.get("message_thread_id"),
                             reply_to=mid, silent=True, timeout=3)
            with open(key, "w", encoding="utf-8") as f:
                f.write(str(ack))
            if gen:
                try:
                    _set_tag(bot, mid, ack=str(ack))
                except Exception:
                    pass
            _typing("attach_ack", bot, mid, ack)
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID={ack}{tag}")
            if not dry_run:
                _timing(bot, mid, t0, ack)
        except Exception as e:
            try:
                os.remove(key)
            except Exception:
                pass
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID=FAILED ({type(e).__name__}){tag}")
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
                  " NONE = no ACK was sent; FAILED = send it yourself as before."
                  " GENERAL_TAG=1 = a tag in General: 👀 is already on it and the ACK is in your topic"
                  " WORK_THREAD_ID; work and reply ONLY there, finish with bot_tg.py general-done; the detailed"
                  " final reply in the topic = reply tool with message_thread_id=WORK_THREAD_ID and"
                  " reply_to=TAG_MSG_ID (it quotes the General tag); stopping to"
                  " ask Omer = bot_tg.py general-wait, NOT general-done."
                  " GENERAL_PENDING_TAG=N = this answers your question on General tag N (now 👀 again):"
                  " give the worker GENERAL_TAG_MSG_ID: N and close N with general-done only when truly done)\n</system-reminder>")
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
    if cmd == "react":
        try:
            react_tag(bot, _arg(argv, "--chat"), _arg(argv, "--msg"), _arg(argv, "--emoji", SEEN))
            print("ok")
        except Exception as e:
            log_react_failure(bot, _arg(argv, "--chat"), _arg(argv, "--msg"), _arg(argv, "--emoji", SEEN), e)
            print(f"[bot_tg] react failed: {e}")
        return 0
    if cmd in ("ask", "ask-wait"):
        try:
            if cmd == "ask":
                out = ask(bot, _arg(argv, "--to"), _arg(argv, "--text"), origin_tag=_arg(argv, "--origin-tag"),
                          wait=float(_arg(argv, "--wait", ASK_DEFAULT_WAIT)))
            else:
                out = ask_wait(bot, _arg(argv, "--id"), timeout=float(_arg(argv, "--timeout", ASK_DEFAULT_WAIT)))
        except ValueError as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
            return 2
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    if cmd == "general-wait":
        print(general_wait(bot, _arg(argv, "--chat"), _arg(argv, "--tag-msg"), _arg(argv, "--question-msg")))
        return 0
    if cmd == "general-done":
        try:
            print(general_done(bot, _arg(argv, "--chat"), _arg(argv, "--tag-msg"), _arg(argv, "--summary"),
                               ok="--fail" not in argv, detail_msg=_arg(argv, "--detail-msg")))
            return 0
        except ValueError as e:
            print(f"[bot_tg] {e}")
            return 2
    if cmd in ("pin", "unpin"):
        # 07/11/2026: through scripts/tg_pin.py (this bot's token, its own registry key) so a finished ACK
        # re-pins the bot's newest live one and the heartbeat clears pins left by a dead worker.
        chat, msg = _arg(argv, "--chat"), _arg(argv, "--msg")
        _pin(cmd, bot, chat, msg)
        print("ok")
        return 0
    if cmd == "delete":
        chat, msg = _arg(argv, "--chat"), _arg(argv, "--msg")
        _pin("unpin", bot, chat, msg)  # every ACK delete unpins first; a no-op when it was not pinned
        method = "deleteMessage"
        try:
            print(json.dumps(api(bot, method, chat_id=chat, message_id=msg)))
        except Exception as e:
            print(f"[bot_tg] {method} failed: {e}")
        if cmd == "delete":
            _typing("end_task", bot, None, msg)
            try:
                close_on_ack_delete(bot, msg)
            except Exception:
                pass
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
