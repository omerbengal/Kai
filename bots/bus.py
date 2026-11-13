#!/usr/bin/env python
"""Request bus between Kai bots (multi-bot stage 2, 05/11/2026; design: docs/multi_bot_separation_design.md s.3).

Since 06/11/2026 (Omer msgs 358 / 361, "חלוקת תפקידים טהורה") the bus serves ONLY the bot that owns the
capability (the WhatsApp bot): its own sends wait here for Omer's button press, recorded by a HOOK (code),
never by a model, and the executor (KaiBusServe) sends them. Other bots no longer use it at all: they ask the
WhatsApp bot visibly by tagging it in the group's General topic (`bot_tg.py ask`, Bot-to-Bot mode on), and
`create` refuses them. The 4-post handoff below is kept for history; with one owner it never fires.

Visibility contract (Omer, 05/11, msg 22360): each request between two bots shows 4 posts, each by the
writing bot in ITS OWN topic, with its own token. The posts are made here, in code, so they cannot be skipped:
  1. sender   (request written / approved)  📤 העברתי ל-X: <summary> - מחכה לעדכון
  2. receiver (executor picks it up)        📥 קיבלתי מ-Y: <summary> - עובד על זה
  3. receiver (done)                        ✅ סיימתי, שולח תשובה ל-Y
  4. sender   (`wait` gets the result)      🔁 X סיים: <summary> - ממשיך
Stall / failure: ⚠️ on both sides. A request not picked up within STALL_S makes `wait` post the stall.
A request a bot makes to its own capability (the WhatsApp bot sending) has no handoff posts.
requests.notified_* columns make every post happen at most once.

CLI (models; bots/guard.py makes sure --from / --bot is the calling bot itself):
  bus.py read   --from B --chat <alias|jid|name> [--limit 50] [--since-hours 24] --summary "..." [--timeout 120]
  bus.py search --from B --query <text> [--chat C] [--limit 30] --summary "..."
  bus.py draft  --from B --chat C --text "<exact text>" | --text-file <utf-8 path> --summary "..."  -> REQUEST_ID,
                busok:N / busno:N, and the stored text + chars (the preview Omer approves). A stray argv token
                (text split by a quote) is refused. A send is "done" only after wa.verify_sent finds the whole text.
  bus.py draft  --from B --chat C --file <path> [--text "<caption>"] --summary "..."     one file + caption
  bus.py draft  --from B --chat C --file P1 --caption C1 --file P2 --caption C2 ... --summary "..."  (max 10,
                sent in this order; signature only under the last one). jpg/jpeg/png/gif/webp go as images,
                any other file (xlsx, pdf, docx, csv...) as a WhatsApp document with its file name (13/11/2026);
                programs, keys and secret-looking names are refused (wa.check_media)
  bus.py wait   --id N --bot B [--timeout 180]
  bus.py status --id N | policy --bot B | audit [--today]
  bus.py check  --for B --chat C [--kind read|send]   read-only yes/no on wa_policy for bot B (WhatsApp bot,
                before serving a General tag; --for, not --bot, so it is not a bus identity)
Code only (guard blocks models from these):
  bus.py serve [--once]          the executor (task KaiBusServe), the only caller of bots/wa.py send
  bus.py approve-hook --bot B    UserPromptSubmit hook: Omer's busok:N / busno:N press
"""
import io
import json
import os
import re
import socket
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "D:/Kai/scripts")
sys.path.insert(0, "D:/Kai/bots")
import kai_bots  # noqa: E402

BUS_DB = "D:/KaiBots/_bus/bus.db"
OMER_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
STALL_S = 300
LATE_DELIVER_S = 180
LOCK_PORT = 8791
RLM = "\u200f"

KIND_CAP = {"wa_read": "whatsapp", "wa_search": "whatsapp", "wa_send": "whatsapp", "wa_send_media": "whatsapp"}
KIND_POLICY = {"wa_read": "read", "wa_search": "read", "wa_send": "send", "wa_send_media": "send"}
SEND_KINDS = ("wa_send", "wa_send_media")
# wa_send_media (07/11/2026, spec by the WhatsApp bot, msg 840): images with captions, in order. A NEW kind and
# not an extension of wa_send, so an executor still running the old code fails it ("unknown kind") instead of
# sending only the captions. The signature goes on the LAST image's caption only.
# 13/11/2026 (Omer, "לשלוח קבצים לוואטסאפ"): the same kind carries documents too (wa.check_media); the bridge
# already sent non-image files as documents, so an old executor would send them the same way.
MAX_MEDIA = 10

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS requests (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      from_bot TEXT NOT NULL, to_cap TEXT NOT NULL, kind TEXT NOT NULL,
      payload_json TEXT NOT NULL, summary TEXT,
      status TEXT NOT NULL,              -- draft | new | picked | done | failed | cancelled | rejected
      approval_id TEXT, created_at TEXT, approved_at TEXT, picked_at TEXT, done_at TEXT,
      result_json TEXT, error TEXT,
      notified_sent INTEGER DEFAULT 0, notified_picked INTEGER DEFAULT 0, notified_done INTEGER DEFAULT 0,
      notified_delivered INTEGER DEFAULT 0, notified_stall INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS audit (
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, request_id INTEGER, actor TEXT, event TEXT, detail TEXT)""",
]


class BusError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def conn(db=None):
    path = db or BUS_DB
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path, timeout=15)
    c.text_factory = str
    c.row_factory = sqlite3.Row
    for s in SCHEMA:
        c.execute(s)
    return c


def audit(c, rid, actor, event, detail=""):
    c.execute("INSERT INTO audit (ts, request_id, actor, event, detail) VALUES (?,?,?,?,?)",
              (now(), rid, actor, event, (detail or "")[:2000]))
    c.commit()


# --- config ---------------------------------------------------------------------------------------

def bots(config_path=None):
    return kai_bots.load_config(config_path or kai_bots.CONFIG)


def owner(cap, config_path=None):
    for name, cfg in bots(config_path).items():
        if cap in cfg.get("caps", []):
            return name
    raise BusError(f"no bot owns capability {cap}")


def title(name, config_path=None):
    t = bots(config_path).get(name, {}).get("title") or name
    return t


def policy(bot, config_path=None):
    return bots(config_path).get(bot, {}).get("wa_policy", {"read": [], "send": []})


_NAME_SEP = re.compile(r"[\s\-־‐-―_]+")


def _name_key(s):
    """'ערב חברים' / 'ערב-חברים' / 'ערב־חברים' / 'ערבחברים' all become 'ערבחברים'."""
    return _NAME_SEP.sub("", (s or "")).lower()


def name_rule_match(bot, chat_name, config_path=None):
    """11/11/2026 (Omer msg 1815): wa_policy.read_name_contains = READ-only rule by chat name. Sends never use
    it: a send needs the chat in wa_policy.send AND Omer's own button, as always."""
    key = _name_key(chat_name)
    if not key:
        return None
    for part in policy(bot, config_path).get("read_name_contains", []):
        if _name_key(part) and _name_key(part) in key:
            return part
    return None


def allowed(bot, kind, chat_jid, config_path=None, chat_name=None):
    import wa
    pol_kind = KIND_POLICY[kind]
    lst = policy(bot, config_path).get(pol_kind, [])
    if "*" in lst:
        return True
    al = wa.aliases(config_path or kai_bots.CONFIG)
    if chat_jid in {al.get(x, x) for x in lst}:
        return True
    if pol_kind == "read" and policy(bot, config_path).get("read_name_contains"):
        if chat_name is None or chat_name == chat_jid:
            try:  # the CURRENT name, so a renamed chat is judged by what it is called now
                chat_name = wa.resolve_chat(chat_jid)[1]
            except Exception:
                return False
        return name_rule_match(bot, chat_name, config_path) is not None
    return False


# --- Telegram posts (each by the writing bot, into its own topic) ---------------------------------

def _default_post(bot, text, silent=True, config_path=None):
    import bot_tg
    cfg = bots(config_path).get(bot, {})
    topics = cfg.get("topics") or []
    if not cfg.get("group_id") or not topics:
        raise BusError(f"{bot} has no group/topic yet")
    return bot_tg.send(bot, cfg["group_id"], text, thread=topics[0], silent=silent)


POST = _default_post


def post_once(c, rid, flag, bot, text, silent=True):
    row = c.execute(f"SELECT {flag} FROM requests WHERE id=?", (rid,)).fetchone()
    if not row or row[0]:
        return False
    c.execute(f"UPDATE requests SET {flag}=1 WHERE id=? AND {flag}=0", (rid,))
    c.commit()
    try:
        POST(bot, RLM + text, silent=silent)
        audit(c, rid, bot, "post_" + flag.split("_", 1)[1], text)
        return True
    except Exception as e:
        audit(c, rid, bot, "post_failed", f"{flag}: {type(e).__name__}: {e}"[:300])
        return False


def handoff(row):
    """True when sender and receiver are different bots (only then the 4 posts are made)."""
    try:
        return row["from_bot"] != owner(row["to_cap"])
    except BusError:
        return True


# --- requests -------------------------------------------------------------------------------------

def check_caller(bot):
    """A bot session runs with KAI_BOT=<name> (start_bot.bat); it may act only as itself."""
    env = os.environ.get("KAI_BOT")
    if env and bot != env:
        raise BusError(f"this session is {env}, not {bot}")
    if bot not in bots():
        raise BusError(f"unknown bot {bot}")


def create(from_bot, kind, payload, summary, db=None):
    """A read/search request (status new, 📤 posted) or a send draft (status draft, waits for Omer's button)."""
    import wa
    if kind not in KIND_CAP:
        raise BusError(f"unknown kind {kind}")
    if KIND_CAP[kind] not in bots().get(from_bot, {}).get("caps", []):
        # 06/11/2026 (Omer msgs 358 / 361, "חלוקת תפקידים טהורה"): no hidden path between bots. A bot without
        # the capability asks its owner visibly, by tagging it in the group's General topic.
        raise BusError(f"{from_bot} has no {KIND_CAP[kind]} capability: ask in General with "
                       f"`bot_tg.py ask --bot {from_bot} --to {owner(KIND_CAP[kind])} --text \"...\"`")
    chat = payload.get("chat")
    if kind != "wa_search" or chat:
        jid, name = wa.resolve_chat(chat)
        payload = dict(payload, chat=jid, chat_name=name)
        if not allowed(from_bot, kind, jid, chat_name=name):
            raise BusError(f"policy: {from_bot} may not {KIND_POLICY[kind]} '{name}' "
                           f"(config/bots.json wa_policy; Omer decides)")
    elif "*" not in policy(from_bot).get("read", []):
        raise BusError("policy: a search over ALL chats needs read '*'; pass --chat")
    if kind == "wa_send_media":
        payload = dict(payload, items=media_items(payload.get("items")))
    status = "draft" if kind in SEND_KINDS else "new"
    c = conn(db)
    cur = c.execute("INSERT INTO requests (from_bot, to_cap, kind, payload_json, summary, status, created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (from_bot, KIND_CAP[kind], kind, json.dumps(payload, ensure_ascii=False),
                     summary or kind, status, now()))
    rid = cur.lastrowid
    c.commit()
    audit(c, rid, from_bot, "created", f"{kind} {status} {json.dumps(payload, ensure_ascii=False)[:500]}")
    row = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if status == "new" and handoff(row):
        post_once(c, rid, "notified_sent", from_bot,
                  f"📤 העברתי ל{title(owner(row['to_cap']))}: {row['summary']} - מחכה לעדכון")
    c.close()
    return rid, payload


def media_items(items):
    """Validated [{file: abs path, caption, name, kind, size_kb}] for wa_send_media (1..MAX_MEDIA files).
    name / kind / size_kb are for the preview Omer approves (which file, sent as image or document)."""
    import wa
    items = list(items or [])
    if not items:
        raise BusError("no file given (--file)")
    if len(items) > MAX_MEDIA:
        raise BusError(f"at most {MAX_MEDIA} files per request")
    out = []
    for it in items:
        try:
            p = wa.check_media(it.get("file"))
            out.append({"file": p, "caption": (it.get("caption") or "").strip(), "name": os.path.basename(p),
                        "kind": wa.media_kind(p), "size_kb": max(1, round(os.path.getsize(p) / 1024))})
        except wa.WaError as e:
            raise BusError(str(e))
    return out


def approve(bot, rid, approval_id, db=None):
    """Called ONLY by the approve-hook (Omer's button press). Returns a status line."""
    c = conn(db)
    row = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not row:
        return f"BUS_APPROVAL id={rid} unknown"
    if row["from_bot"] != bot:
        audit(c, rid, bot, "approve_refused", "not this bot's request")
        return f"BUS_APPROVAL id={rid} refused (belongs to {row['from_bot']})"
    if row["status"] != "draft":
        return f"BUS_APPROVAL id={rid} ignored (status {row['status']})"
    c.execute("UPDATE requests SET status='new', approval_id=?, approved_at=? WHERE id=? AND status='draft'",
              (approval_id, now(), rid))
    c.commit()
    audit(c, rid, "omer", "approved", approval_id)
    if handoff(row):
        post_once(c, rid, "notified_sent", bot,
                  f"📤 העברתי ל{title(owner(row['to_cap']))}: {row['summary']} - מחכה לעדכון")
    c.close()
    return f"BUS_APPROVAL id={rid} approved"


def cancel(bot, rid, db=None):
    c = conn(db)
    row = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not row or row["from_bot"] != bot or row["status"] != "draft":
        return f"BUS_APPROVAL id={rid} cancel ignored"
    c.execute("UPDATE requests SET status='cancelled', done_at=? WHERE id=?", (now(), rid))
    c.commit()
    audit(c, rid, "omer", "cancelled", "")
    return f"BUS_APPROVAL id={rid} cancelled"


def execute(row, wa_mod=None):
    import wa
    wa_mod = wa_mod or wa
    p = json.loads(row["payload_json"])
    if p.get("chat"):
        ok = allowed(row["from_bot"], row["kind"], p["chat"])
    else:
        ok = "*" in policy(row["from_bot"]).get("read", [])
    if not ok:  # policy is checked again at execution: Omer may have narrowed it after the request
        raise BusError("policy: not allowed (anymore)")
    if row["kind"] == "wa_read":
        return wa_mod.read(p["chat"], p.get("limit", 50), p.get("since_hours"))
    if row["kind"] == "wa_search":
        return wa_mod.search(p["query"], p.get("chat"), p.get("limit", 30))
    if row["kind"] == "wa_send":
        if not row["approval_id"]:
            raise BusError("no approval recorded - refusing to send")
        res = wa_mod.send(p["chat"], p["text"])
        try:  # 08/11/2026: "done" only when WhatsApp holds the whole approved text
            check = wa_mod.verify_sent(p["chat"], p["text"])
        except Exception as e:
            raise BusError(f"נשלח אבל לא במלואו / לא אומת - לא לדווח 'נשלח' ({e})")
        return dict(res, chat=p.get("chat_name"), verified=check.get("chars"))
    if row["kind"] == "wa_send_media":
        if not row["approval_id"]:
            raise BusError("no approval recorded - refusing to send")
        items = p.get("items") or []
        sent = []
        for i, it in enumerate(items):
            try:
                sent.append(wa_mod.send_media(p["chat"], it["file"], it.get("caption", ""),
                                              sign=(i == len(items) - 1)))
            except Exception as e:  # no automatic retry: a half-sent series must be looked at, not repeated
                raise BusError(f"נשלחו {len(sent)} מתוך {len(items)} ({type(e).__name__}: {e})"[:400])
        return {"sent": True, "count": len(sent), "chat": p.get("chat_name"),
                "files": [s.get("file") for s in sent], "kinds": [s.get("kind") for s in sent]}
    raise BusError(f"unknown kind {row['kind']}")


def serve_once(db=None, wa_mod=None):
    """Handle every new request once; post the late 🔁/⚠️ for results nobody waited for."""
    c = conn(db)
    handled = 0
    for row in c.execute("SELECT * FROM requests WHERE status='new' ORDER BY id").fetchall():
        cur = c.execute("UPDATE requests SET status='picked', picked_at=? WHERE id=? AND status='new'",
                        (now(), row["id"]))
        c.commit()
        if cur.rowcount != 1:
            continue
        rid, recv, sender = row["id"], owner(row["to_cap"]), row["from_bot"]
        audit(c, rid, recv, "picked", "")
        ho = handoff(row)
        if ho:
            post_once(c, rid, "notified_picked", recv, f"📥 קיבלתי מ{title(sender)}: {row['summary']} - עובד על זה")
        try:
            res = execute(row, wa_mod)
            c.execute("UPDATE requests SET status='done', done_at=?, result_json=? WHERE id=?",
                      (now(), json.dumps(res, ensure_ascii=False), rid))
            c.commit()
            audit(c, rid, recv, "done", json.dumps(res, ensure_ascii=False)[:300])
            if ho:
                post_once(c, rid, "notified_done", recv, f"✅ סיימתי, שולח תשובה ל{title(sender)}")
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:400]
            c.execute("UPDATE requests SET status='failed', done_at=?, error=? WHERE id=?", (now(), err, rid))
            c.commit()
            audit(c, rid, recv, "failed", err)
            post_once(c, rid, "notified_done", recv, f"⚠️ נכשל: {row['summary']} - {err}", silent=False)
        handled += 1
    # Sender-side post for results nobody collected (the sender's session died or moved on).
    for row in c.execute("SELECT * FROM requests WHERE status IN ('done','failed') AND notified_delivered=0"
                         " AND done_at <= datetime('now', ?)", (f"-{LATE_DELIVER_S} seconds",)).fetchall():
        if handoff(row):
            deliver_post(c, row)
        else:
            c.execute("UPDATE requests SET notified_delivered=1 WHERE id=?", (row["id"],))
            c.commit()
    c.close()
    return handled


def deliver_post(c, row):
    recv = title(owner(row["to_cap"]))
    if row["status"] == "done":
        return post_once(c, row["id"], "notified_delivered", row["from_bot"], f"🔁 {recv} סיים: {row['summary']} - ממשיך")
    return post_once(c, row["id"], "notified_delivered", row["from_bot"],
                     f"⚠️ {recv} נכשל: {row['summary']} - {row['error']}", silent=False)


def wait(rid, bot, timeout=180, db=None, sleep=time.sleep, clock=time.time, stall_s=STALL_S):
    c = conn(db)
    start = clock()
    while True:
        row = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        if not row:
            raise BusError(f"no request {rid}")
        if row["from_bot"] != bot:
            raise BusError(f"request {rid} belongs to {row['from_bot']}")
        st = row["status"]
        if st in ("done", "failed"):
            if handoff(row):
                deliver_post(c, row)
            else:
                c.execute("UPDATE requests SET notified_delivered=1 WHERE id=?", (rid,))
                c.commit()
            out = {"id": rid, "status": st}
            if st == "done":
                out["result"] = json.loads(row["result_json"] or "null")
            else:
                out["error"] = row["error"]
            c.close()
            return out
        if st in ("draft", "cancelled", "rejected"):
            c.close()
            return {"id": rid, "status": st,
                    "note": "waiting for Omer's button" if st == "draft" else "not executed"}
        waited = clock() - start
        if st == "new" and waited >= stall_s and handoff(row):
            post_once(c, rid, "notified_stall", bot,
                      f"⚠️ {title(owner(row['to_cap']))} לא הרים את הבקשה: {row['summary']} - תקוע", silent=False)
        if waited >= timeout:
            c.close()
            return {"id": rid, "status": "timeout", "last_status": st}
        sleep(1.5)


# --- approve hook ---------------------------------------------------------------------------------

def approve_hook(bot, prompt, db=None):
    import re
    import auto_ack as main_ack
    cfg = bots().get(bot, {})
    lines = []
    for attrs, content in main_ack.parse_channels(prompt):
        data = attrs.get("callback_data", "")
        m = re.match(r"^bus(ok|no):(\d+)$", data)
        if not m:
            continue
        if attrs.get("user_id") != OMER_ID or attrs.get("chat_id") not in {str(cfg.get("group_id")), OMER_ID}:
            lines.append(f"BUS_APPROVAL id={m.group(2)} refused (not Omer / not this bot's chat)")
            continue
        rid = int(m.group(2))
        if m.group(1) == "ok":
            lines.append(approve(bot, rid, f"tg_callback:{attrs.get('chat_id')}:{attrs.get('message_id')}:"
                                           f"{attrs.get('ts', '')}", db))
        else:
            lines.append(cancel(bot, rid, db))
    return lines


# --- CLI ------------------------------------------------------------------------------------------

def _arg(argv, flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv else default


def parse_media_args(argv):
    """--file P [--caption C] pairs, in order; a single --file may take its caption from --text."""
    items = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--file" and i + 1 < len(argv):
            items.append({"file": argv[i + 1], "caption": ""})
            i += 2
            continue
        if a == "--caption" and i + 1 < len(argv):
            if not items or items[-1]["caption"]:
                raise BusError("--caption must follow its --file")
            items[-1]["caption"] = argv[i + 1]
            i += 2
            continue
        i += 1
    if len(items) == 1 and not items[0]["caption"] and _arg(argv, "--text"):
        items[0]["caption"] = _arg(argv, "--text")
    return items


DRAFT_VALUE_FLAGS = ("--from", "--chat", "--text", "--text-file", "--summary", "--file", "--caption", "--timeout")


def check_draft_argv(argv):
    """Refuse a draft whose text was split by shell quoting.

    08/11/2026 (Tal, Vienna plan): `--text "...לחפש "דירת מלון" עם..."` - the inner quotes closed the shell
    string, argv got the text cut at 'לחפש דירת' plus stray words, and 468 of 2109 chars were sent.
    Every token after the command must be a known flag or that flag's value."""
    i = 2
    while i < len(argv):
        a = argv[i]
        if a in DRAFT_VALUE_FLAGS:
            i += 2
            continue
        raise BusError(f"stray argument {a[:40]!r}: the text was split by shell quoting (a quote inside --text?)."
                       " Write the text to a UTF-8 file and pass --text-file <path>")
    if "--text" in argv and "--text-file" in argv:
        raise BusError("pass --text or --text-file, not both")


def read_text_file(path):
    with open(path, encoding="utf-8-sig") as f:
        return f.read().strip()


def _single_instance():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        return None


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    out = None
    try:
        if cmd == "approve-hook":
            try:
                data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
                lines = approve_hook(_arg(argv, "--bot"), data.get("prompt", "") or data.get("content", ""))
            except Exception as e:
                lines = [f"BUS_APPROVAL hook error {type(e).__name__}"]
            if lines:
                print("<system-reminder>\n" + "\n".join(lines) +
                      "\n(bus approve hook: Omer's button was recorded by code. approved = run "
                      "`bus.py wait --id N --bot <you>` and report; cancelled = tell Omer it was not sent)\n"
                      "</system-reminder>")
            return 0
        if cmd == "serve":
            lock = _single_instance()
            if not lock:
                print("another bus executor is running")
                return 0
            while True:
                try:
                    serve_once()
                except Exception as e:  # one bad row or a locked DB must not kill the executor
                    try:
                        c = conn()
                        audit(c, None, "executor", "loop_error", f"{type(e).__name__}: {e}")
                        c.close()
                    except Exception:
                        pass
                if "--once" in argv:
                    return 0
                time.sleep(1.5)
        if cmd in ("read", "search", "draft"):
            frm = _arg(argv, "--from")
            check_caller(frm)
            if cmd == "read":
                payload = {"chat": _arg(argv, "--chat"), "limit": int(_arg(argv, "--limit", 50)),
                           "since_hours": _arg(argv, "--since-hours")}
                kind = "wa_read"
            elif cmd == "search":
                payload = {"query": _arg(argv, "--query"), "chat": _arg(argv, "--chat"),
                           "limit": int(_arg(argv, "--limit", 30))}
                kind = "wa_search"
            else:
                check_draft_argv(argv)
                if "--text-file" in argv:
                    argv = list(argv) + ["--text", read_text_file(_arg(argv, "--text-file"))]
            if cmd == "draft" and "--file" in argv:
                payload = {"chat": _arg(argv, "--chat"), "items": parse_media_args(argv)}
                kind = "wa_send_media"
            elif cmd == "draft":
                text = _arg(argv, "--text")
                if not (text or "").strip():
                    raise BusError("--text is required")
                payload = {"chat": _arg(argv, "--chat"), "text": text}
                kind = "wa_send"
            rid, p = create(frm, kind, payload, _arg(argv, "--summary", kind))
            if cmd == "draft":
                out = {"request_id": rid, "chat": p.get("chat_name"), "status": "draft",
                       "items": p.get("items"), "text": p.get("text"),
                       "chars": len(p.get("text") or ""),
                       "check": "the preview you show Omer is THIS text; if chars differ from your text, cancel",
                       "buttons": [{"text": "✅ שלח", "callback_data": f"busok:{rid}"},
                                   {"text": "❌ בטל", "callback_data": f"busno:{rid}"}],
                       "next": "show Omer the exact text + these buttons; after BUS_APPROVAL approved run wait"}
            else:
                out = wait(rid, frm, int(_arg(argv, "--timeout", 120)))
        elif cmd == "wait":
            b = _arg(argv, "--bot")
            check_caller(b)
            out = wait(int(_arg(argv, "--id")), b, int(_arg(argv, "--timeout", 180)))
        elif cmd == "status":
            c = conn()
            r = c.execute("SELECT id, from_bot, kind, summary, status, created_at, done_at, error FROM requests"
                          " WHERE id=?", (int(_arg(argv, "--id")),)).fetchone()
            out = dict(r) if r else {"error": "no such request"}
        elif cmd == "policy":
            b = _arg(argv, "--bot")
            out = {"bot": b, "wa_policy": policy(b), "aliases": __import__("wa").aliases()}
        elif cmd == "check":  # read-only yes/no: may the WhatsApp bot read / send chat C for bot B's request?
            b = _arg(argv, "--for")
            kind = {"read": "wa_read", "send": "wa_send"}[_arg(argv, "--kind", "read")]
            jid, name = __import__("wa").resolve_chat(_arg(argv, "--chat"))
            ok = allowed(b, kind, jid, chat_name=name)
            out = {"for": b, "kind": KIND_POLICY[kind], "chat": jid, "chat_name": name, "allowed": ok,
                   "by_name_rule": name_rule_match(b, name) if ok and kind == "wa_read" else None}
        elif cmd == "audit":
            c = conn()
            q = "SELECT ts, request_id, actor, event, substr(detail,1,120) d FROM audit"
            if "--today" in argv:
                q += " WHERE ts >= date('now')"
            out = [dict(r) for r in c.execute(q + " ORDER BY id DESC LIMIT 100").fetchall()]
        else:
            print(__doc__)
            return 1
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    if sys.stdout is None:  # pythonw (task KaiBusServe) has no console
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    else:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.exit(main(sys.argv))
