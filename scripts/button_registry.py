#!/usr/bin/env python
"""Open-buttons registry + cleanup (Omer, 07/11/2026, General msgs 596/599).

Every Kai message sent WITH inline buttons is recorded here. When the question is answered - a button
press, a Telegram reply to it, or a plain message that answers it - the buttons are removed and the
message gets one more line:

    ‏✅ נבחר: <what Omer chose>

so no question is left with live buttons (a stale button shows a loading spinner when pressed late).

Where entries come from (all automatic, nobody calls register by hand):
  * the reply tool       - PostToolUse hook save_telegram_message.py --mode outgoing (main Kai) and
                           bots/bot_tg.py save --mode outgoing (domain bots) call on_outgoing()
  * Bot API senders      - scripts/topic_send.py registers after a send with a keyboard
What closes them:
  * a press              - the incoming hook (on_incoming) closes it with the pressed button's label
  * a reply / plain text - on_incoming closes it by itself only when the text IS one of the buttons
                           (or a plain yes/no to a yes/no question); anything that needs judgment is the
                           dispatcher's call: `close` below, with the chosen option in Omer's words.

Storage: table open_buttons in kai.db (main Kai) or in the bot's own bot.db (domain bots).

CLI:
  button_registry.py open  [--bot main] [--chat C]          open questions (JSON lines)
  button_registry.py match [--bot main] --chat C --text T [--reply-to M]
                                                            which open question this text answers (JSON)
  button_registry.py close [--bot main] --chat C --msg M --chosen "..."
                                                            remove the buttons + append the ✅ line
  button_registry.py register [--bot main] --chat C --msg M --text T --keyboard JSON
"""
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import net_ipv4  # noqa: F401  IPv6 to api.telegram.org is slow on this PC
except Exception:
    pass

MAIN_DB = "D:/Kai/kai.db"
OMER_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
RLM = "\u200f"
CHOSEN_PREFIX = f"{RLM}✅ נבחר: "
LIST_MAX_AGE = timedelta(days=7)      # older entries are not offered as candidates
AUTO_MAX_AGE = timedelta(hours=48)    # plain-message auto close only for recent questions

SCHEMA = """CREATE TABLE IF NOT EXISTS open_buttons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot TEXT NOT NULL DEFAULT 'main',
  chat_id TEXT NOT NULL,
  message_id INTEGER NOT NULL,
  text TEXT,
  keyboard TEXT,
  parse_mode TEXT,
  source TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  chosen TEXT,
  created_at TEXT NOT NULL,
  closed_at TEXT,
  UNIQUE(bot, chat_id, message_id)
)"""

YES_WORDS = {"כן", "כן כן", "סבבה", "יאללה", "תעשה", "תעשה את זה", "אוקיי", "אוקי", "ok", "okay", "yes",
             "בטח", "מאשר", "אישור", "קדימה", "לך על זה", "בסדר", "נכון", "👍", "✅"}
NO_WORDS = {"לא", "no", "עזוב", "אל תעשה", "לא צריך", "לא תודה", "בטל", "לא עכשיו", "👎", "❌"}
_YES_LABEL = re.compile(r"^(כן|אשר|מאשר|שלח|תשלח|תעשה|יאללה|סבבה|בצע|לבצע|ok|yes)\b", re.I)
_NO_LABEL = re.compile(r"^(לא|עזוב|בטל|דלג|אל |no\b)", re.I)


# ---------------------------------------------------------------- helpers
def now_iso():
    return datetime.now(timezone.utc).isoformat()


def label_core(label):
    """'✅ כן, תרסטרט' -> 'כן, תרסטרט' (leading emoji/marks dropped; same rule as the plugin patch)."""
    return re.sub(r"^[^\w]+", "", (label or "").replace(RLM, "")).strip() or (label or "").strip()


def norm(s):
    s = (s or "").replace(RLM, "").replace("\u200e", "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def buttons(keyboard):
    if isinstance(keyboard, str):
        try:
            keyboard = json.loads(keyboard)
        except Exception:
            return []
    out = []
    for row in keyboard or []:
        for b in row or []:
            if isinstance(b, dict) and b.get("text"):
                out.append(b)
    return out


def label_for(keyboard, callback_data):
    for b in buttons(keyboard):
        if b.get("callback_data") == callback_data:
            return label_core(b["text"])
    return None


def polarity(label):
    raw = (label or "").replace(RLM, "").strip()
    if raw[:1] in ("✅", "👍", "✔"):
        return "yes"
    if raw[:1] in ("❌", "👎", "✖", "🚫"):
        return "no"
    core = label_core(label)
    if _NO_LABEL.match(core):
        return "no"
    if _YES_LABEL.match(core):
        return "yes"
    return None


def label_hit(entry, text):
    """The button this text names: exact label, or a plain yes/no to a question with one yes and one no."""
    t = norm(text)
    if not t:
        return None
    btns = buttons(entry.get("keyboard"))
    for b in btns:
        if norm(label_core(b["text"])) == t or norm(b["text"]) == t:
            return label_core(b["text"])
    raw = (text or "").replace(RLM, "").strip()
    want = "yes" if (t in YES_WORDS or raw in YES_WORDS) else "no" if (t in NO_WORDS or raw in NO_WORDS) else None
    if want:
        hits = [b for b in btns if polarity(b["text"]) == want]
        if len(hits) == 1:
            return label_core(hits[0]["text"])
    return None


def _parse_ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ---------------------------------------------------------------- storage
def db_path_for(bot="main"):
    if not bot or bot == "main":
        return MAIN_DB
    import kai_bots
    return os.path.join(kai_bots.bot(bot)["home"], "bot.db")


def _conn(db):
    c = sqlite3.connect(db, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute(SCHEMA)
    return c


def register(chat_id, message_id, text, keyboard, bot="main", parse_mode=None, source=None, db=None):
    if not buttons(keyboard) or message_id is None:
        return False
    c = _conn(db or db_path_for(bot))
    c.execute("INSERT OR REPLACE INTO open_buttons (bot, chat_id, message_id, text, keyboard, parse_mode, source,"
              " status, created_at) VALUES (?,?,?,?,?,?,?, 'open', ?)",
              (bot or "main", str(chat_id), int(message_id), text,
               keyboard if isinstance(keyboard, str) else json.dumps(keyboard, ensure_ascii=False),
               parse_mode, source, now_iso()))
    c.commit()
    c.close()
    return True


def get(chat_id, message_id, bot="main", db=None):
    c = _conn(db or db_path_for(bot))
    r = c.execute("SELECT * FROM open_buttons WHERE bot=? AND chat_id=? AND message_id=?",
                  (bot or "main", str(chat_id), int(message_id))).fetchone()
    c.close()
    return dict(r) if r else None


def list_open(chat_id=None, bot="main", db=None, max_age=LIST_MAX_AGE, now=None):
    c = _conn(db or db_path_for(bot))
    q = "SELECT * FROM open_buttons WHERE bot=? AND status='open'"
    args = [bot or "main"]
    if chat_id is not None:
        q += " AND chat_id=?"
        args.append(str(chat_id))
    rows = [dict(r) for r in c.execute(q + " ORDER BY created_at DESC, id DESC", args)]
    c.close()
    now = now or datetime.now(timezone.utc)
    return [r for r in rows if (_parse_ts(r["created_at"]) or now) >= now - max_age]


def _mark(chat_id, message_id, status, chosen, bot, db):
    c = _conn(db or db_path_for(bot))
    c.execute("UPDATE open_buttons SET status=?, chosen=?, closed_at=? WHERE bot=? AND chat_id=? AND message_id=?",
              (status, chosen, now_iso(), bot or "main", str(chat_id), int(message_id)))
    c.commit()
    c.close()


# ---------------------------------------------------------------- Telegram
class TgError(Exception):
    pass


def token_for(bot="main"):
    if not bot or bot == "main":
        import tg_silent_send
        return tg_silent_send.BOT_TOKEN
    sys.path.insert(0, "D:/Kai/bots")
    import bot_tg
    return bot_tg.token(bot)


def make_api(bot="main"):
    tok = token_for(bot)

    def api(method, **params):
        req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/{method}",
                                     data=json.dumps(params, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise TgError(e.read().decode("utf-8", "replace")[:300])
        if not data.get("ok"):
            raise TgError(str(data)[:300])
        return data.get("result")
    return api


_MD2 = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def chosen_line(chosen, parse_mode=None):
    line = CHOSEN_PREFIX + chosen
    return _MD2.sub(r"\\\1", line) if (parse_mode or "").lower() == "markdownv2" else line


def close(chat_id, message_id, chosen, bot="main", force_text=False, text=None, api=None, db=None):
    """Remove the buttons and append the ✅ line. Returns 'closed' | 'gone' | 'already' | 'error: ...'.

    The buttons are removed FIRST with editMessageReplyMarkup: if Telegram says the message has none any
    more (someone already edited it, e.g. a worker replaced the question with its result), the text is
    left alone - re-writing it from the registry would undo that edit. force_text skips that probe: a press
    (the plugin has just rewritten the text itself) or a message we know still holds the question."""
    entry = get(chat_id, message_id, bot, db)
    if entry and entry["status"] != "open" and not force_text:
        return "already"
    api = api or make_api(bot)
    chosen = (chosen or "").strip()
    removed = True
    if not force_text:
        try:
            api("editMessageReplyMarkup", chat_id=str(chat_id), message_id=int(message_id))
        except TgError as e:
            removed = False
            if "not modified" not in str(e) and "not found" not in str(e) and "can't be edited" not in str(e):
                if entry:
                    _mark(chat_id, message_id, "open", None, bot, db)
                return f"error: {e}"
    orig = text if text is not None else (entry or {}).get("text")
    if removed and orig and chosen:
        pm = (entry or {}).get("parse_mode")
        params = {"chat_id": str(chat_id), "message_id": int(message_id),
                  "text": f"{orig}\n\n{chosen_line(chosen, pm)}"}
        if (pm or "").lower() == "markdownv2":
            params["parse_mode"] = "MarkdownV2"
        try:
            api("editMessageText", **params)
        except TgError as e:
            if "not modified" not in str(e):
                if entry:
                    _mark(chat_id, message_id, "closed", chosen, bot, db)
                return f"error: {e}"
    if entry:
        _mark(chat_id, message_id, "closed" if removed else "gone", chosen or None, bot, db)
    return "closed" if removed else "gone"


# ---------------------------------------------------------------- matching
def match(chat_id, text, reply_to=None, bot="main", db=None, now=None):
    """Which open question does this text answer?

    -> {"status": "high", "message_id", "chosen"}        safe to close automatically
       {"status": "reply", "message_id", "chosen": None}  a reply to it, but not one of the buttons:
                                                         the dispatcher decides (answer, or a question about it)
       {"status": "ambiguous" | "none", "candidates": [...]}  plain text: the dispatcher judges by content
    """
    now = now or datetime.now(timezone.utc)
    if reply_to:
        e = get(chat_id, reply_to, bot, db)
        if e and e["status"] == "open":
            hit = label_hit(e, text)
            return {"status": "high" if hit else "reply", "message_id": e["message_id"], "chosen": hit}
    entries = list_open(chat_id, bot, db, now=now)
    cands = [{"message_id": e["message_id"], "text": (e["text"] or "")[:120],
              "labels": [label_core(b["text"]) for b in buttons(e["keyboard"])],
              "created_at": e["created_at"]} for e in entries]
    recent = [e for e in entries if (_parse_ts(e["created_at"]) or now) >= now - AUTO_MAX_AGE]
    hits = [(e, label_hit(e, text)) for e in recent]
    hits = [(e, h) for e, h in hits if h]
    if len(hits) == 1:
        e, h = hits[0]
        exact = any(norm(label_core(b["text"])) == norm(text) for b in buttons(e["keyboard"]))
        # a bare yes/no only counts for the newest open question in this chat
        if exact or (recent and recent[0]["message_id"] == e["message_id"]):
            return {"status": "high", "message_id": e["message_id"], "chosen": h}
    return {"status": "ambiguous" if len(hits) > 1 else "none", "candidates": cands}


# ---------------------------------------------------------------- hooks
def _channel(prompt):
    m = re.search(r"<channel([\s\S]*?)>([\s\S]*?)</channel>", prompt or "")
    if not m:
        return None
    attrs, body = m.group(1), m.group(2).strip()

    def attr(n):
        a = re.search(rf'\b{n}="(.*?)"', attrs, re.DOTALL)
        return a.group(1) if a else None
    return {"chat_id": attr("chat_id"), "message_id": attr("message_id"), "user_id": attr("user_id"),
            "reply_to": attr("reply_to_message_id"), "callback_data": attr("callback_data"), "text": body}


def on_incoming(prompt, bot="main", api=None, db=None):
    """UserPromptSubmit: a press closes its entry; a text that IS a button closes its question."""
    c = _channel(prompt)
    if not c or not c["chat_id"]:
        return None
    if c["user_id"] and c["user_id"] != OMER_ID:
        return None  # another bot tagging in General, not Omer's answer
    if c["callback_data"] is not None and c["message_id"]:
        e = get(c["chat_id"], c["message_id"], bot, db)
        if not e or e["status"] != "open":
            return None  # the plugin already removed the buttons and wrote the label itself
        label = label_for(e["keyboard"], c["callback_data"]) or c["callback_data"]
        return close(c["chat_id"], c["message_id"], label, bot, force_text=True, api=api, db=db)
    if c["text"].startswith("[callback:"):
        return None
    m = match(c["chat_id"], c["text"], c["reply_to"], bot, db)
    if m["status"] == "high":
        return close(c["chat_id"], m["message_id"], m["chosen"], bot, api=api, db=db)
    return None


def on_outgoing(tool_input, response_str, bot="main", db=None):
    """PostToolUse on reply: record a message sent with inline_keyboard.

    The plugin puts the keyboard on the LAST text chunk; files go out after the text."""
    kb = (tool_input or {}).get("inline_keyboard")
    if not buttons(kb):
        return None
    ids = []
    m = re.search(r"sent \(id: (\d+)\)", response_str or "")
    if m:
        ids = [int(m.group(1))]
    else:
        m = re.search(r"sent \d+ parts \(ids: ([\d,\s]+)\)", response_str or "")
        if m:
            ids = [int(x) for x in re.findall(r"\d+", m.group(1))]
    if not ids:
        return None
    n_files = min(len(tool_input.get("files") or []), len(ids))
    n_text = len(ids) - n_files
    if n_text < 1:
        return None
    mid = ids[n_text - 1]
    text = tool_input.get("text") if n_text == 1 else None  # a split text: we do not know the last chunk
    pm = "markdownv2" if (tool_input.get("format") or "").lower() == "markdownv2" else None
    register(tool_input.get("chat_id"), mid, text, kb, bot, pm, "reply_tool", db)
    return mid


# ---------------------------------------------------------------- CLI
def _arg(argv, flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else default


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    bot = _arg(argv, "--bot", "main")
    chat = _arg(argv, "--chat")
    if cmd == "open":
        for e in list_open(chat, bot):
            print(json.dumps({"chat_id": e["chat_id"], "message_id": e["message_id"],
                              "created_at": e["created_at"], "text": (e["text"] or "")[:120],
                              "labels": [label_core(b["text"]) for b in buttons(e["keyboard"])]},
                             ensure_ascii=False))
        return 0
    if cmd == "match":
        print(json.dumps(match(chat, _arg(argv, "--text", ""), _arg(argv, "--reply-to"), bot), ensure_ascii=False))
        return 0
    if cmd == "close":
        print(close(chat, _arg(argv, "--msg"), _arg(argv, "--chosen", ""), bot, force_text="--force" in argv))
        return 0
    if cmd == "register":
        print(register(chat, _arg(argv, "--msg"), _arg(argv, "--text"), _arg(argv, "--keyboard"), bot,
                       source="cli"))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main(sys.argv))
