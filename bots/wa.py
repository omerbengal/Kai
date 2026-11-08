#!/usr/bin/env python
"""WhatsApp access for the Kai WhatsApp bot and the bus executor (multi-bot stage 2, 05/11/2026).

Only two callers may use this module: the WhatsApp bot's own session (caps "whatsapp" in config/bots.json)
and `bots/bus.py serve` (the executor that answers other bots' requests). Every other bot is blocked from it
by bots/guard.py. There is deliberately NO send command in the CLI: every WhatsApp send goes through
`bus.py draft` + Omer's button (the approval is written by a hook, never by a model).

CLI (WhatsApp bot only):
  wa.py chats  --query <name part> [--limit 20]
  wa.py read   --chat <alias|jid|name> [--limit 50] [--since-hours 24]
  wa.py search --query <text> [--chat <alias|jid|name>] [--limit 30]
  wa.py aliases
"""
import io
import json
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "D:/Kai/scripts")
import kai_bots  # noqa: E402

WA_DB = "D:/Kai/whatsapp-mcp/whatsapp-bridge/store/messages.db"
BRIDGE = "http://localhost:8080"
SIGNATURE = "[נשלח על ידי קאי 🤖]"
MAX_TEXT = 1500


class WaError(Exception):
    pass


def aliases(config_path=kai_bots.CONFIG):
    try:
        with open(config_path, encoding="utf-8") as f:
            return json.load(f).get("wa_aliases", {})
    except Exception:
        return {}


def _db(db_path=None):
    conn = sqlite3.connect(f"file:{db_path or WA_DB}?mode=ro", uri=True, timeout=10)
    conn.text_factory = str
    return conn


def resolve_chat(chat, db_path=None, config_path=kai_bots.CONFIG):
    """(jid, name). Accepts a JID, an alias from config/bots.json wa_aliases, or a unique chat-name part."""
    chat = (chat or "").strip()
    if not chat:
        raise WaError("no chat given")
    al = aliases(config_path)
    jid = al.get(chat) or (chat if "@" in chat else None)
    conn = _db(db_path)
    try:
        if jid:
            row = conn.execute("SELECT name FROM chats WHERE jid=?", (jid,)).fetchone()
            return jid, (row[0] if row and row[0] else jid)
        rows = conn.execute("SELECT jid, name FROM chats WHERE name LIKE ? ORDER BY last_message_time DESC LIMIT 6",
                            (f"%{chat}%",)).fetchall()
    finally:
        conn.close()
    exact = [r for r in rows if (r[1] or "").strip() == chat]
    if len(exact) == 1:
        return exact[0][0], exact[0][1]
    if len(rows) == 1:
        return rows[0][0], rows[0][1]
    if not rows:
        raise WaError(f"no WhatsApp chat matches '{chat}'")
    raise WaError("ambiguous chat name, candidates: " + " | ".join(r[1] or r[0] for r in rows))


def alias_of(jid, config_path=kai_bots.CONFIG):
    return next((a for a, j in aliases(config_path).items() if j == jid), None)


def _fmt_ts(ts):
    t = str(ts)
    try:  # the bridge writes its own sends with 7 fraction digits (12:07:11.0386795+02:00), py3.9 takes max 6
        t = re.sub(r"(\.\d{6})\d+", r"", t)
        return datetime.fromisoformat(t).strftime("%d/%m %H:%M")
    except Exception:
        return str(ts)


def chat_jids(jid, device_db=None):
    """Every JID of the same private chat: the phone JID and its LID twin (bridge whatsmeow_lid_map).

    08/11/2026: a contact that migrated to a LID keeps a NAMED phone-JID chat holding only Kai's own
    sends, while every reply of hers lands in an unnamed <lid>@lid chat. Reading one JID alone said "no reply"
    while she had answered all 3 questions. Groups / newsletters are returned as is."""
    user, _, server = (jid or "").partition("@")
    if server not in ("s.whatsapp.net", "lid") or not user:
        return [jid]
    out = [jid]
    try:
        dev = sqlite3.connect(f"file:{device_db or WA_DEVICE_DB}?mode=ro", uri=True, timeout=5)
        try:
            if server == "lid":
                rows = dev.execute("SELECT pn FROM whatsmeow_lid_map WHERE lid=?", (user,)).fetchall()
                out += [f"{r[0]}@s.whatsapp.net" for r in rows if r[0]]
            else:
                rows = dev.execute("SELECT lid FROM whatsmeow_lid_map WHERE pn=?", (user,)).fetchall()
                out += [f"{r[0]}@lid" for r in rows if r[0]]
        finally:
            dev.close()
    except Exception:
        pass
    return list(dict.fromkeys(out))


def _sender_names(conn, senders):
    out = {}
    for s in senders:
        if not s:
            continue
        cand = [s, f"{s}@s.whatsapp.net", f"{s}@lid"] if "@" not in s else [s]
        name = None
        for c in cand:
            row = conn.execute("SELECT name FROM chats WHERE jid=?", (c,)).fetchone()
            if row and row[0] and not row[0].split("@")[0].lstrip("+").isdigit():  # a bare number is no name
                name = row[0]
                break
        out[s] = name or _contact_name(s) or s.split("@")[0]
    return out


WA_DEVICE_DB = "D:/Kai/whatsapp-mcp/whatsapp-bridge/store/whatsapp.db"
KAI_DB = "D:/Kai/kai.db"


def _contact_name(sender):
    """Group senders are often LIDs: LID -> phone (bridge lid map) -> name in kai.db (people, phone_contacts)."""
    user = sender.split("@")[0]
    phone = user
    try:
        dev = sqlite3.connect(f"file:{WA_DEVICE_DB}?mode=ro", uri=True, timeout=5)
        row = dev.execute("SELECT pn FROM whatsmeow_lid_map WHERE lid=?", (user,)).fetchone()
        dev.close()
        if row:
            phone = row[0]
    except Exception:
        pass
    tail = phone[-9:]
    if len(tail) < 9:
        return None
    try:
        k = sqlite3.connect(f"file:{KAI_DB}?mode=ro", uri=True, timeout=5)
        k.text_factory = str
        for q in ("SELECT name FROM people WHERE replace(replace(replace(phone,'-',''),' ',''),'+','') LIKE ?",
                  "SELECT name FROM phone_contacts WHERE replace(replace(replace(phone,'-',''),' ',''),'+','') LIKE ?"):
            row = k.execute(q + " LIMIT 1", (f"%{tail}",)).fetchone()
            if row and row[0]:
                k.close()
                return row[0]
        k.close()
    except Exception:
        pass
    try:  # the name the person set in WhatsApp (push name) or Omer's phonebook name, from the bridge
        dev = sqlite3.connect(f"file:{WA_DEVICE_DB}?mode=ro", uri=True, timeout=5)
        dev.text_factory = str
        for jid in (f"{user}@lid", f"{phone}@s.whatsapp.net"):
            row = dev.execute("SELECT full_name, push_name FROM whatsmeow_contacts WHERE their_jid=? LIMIT 1",
                              (jid,)).fetchone()
            if row and (row[0] or row[1]):
                dev.close()
                return row[0] or row[1]
        dev.close()
    except Exception:
        pass
    return "+" + phone if phone != user else None


def _rows_to_msgs(conn, rows):
    names = _sender_names(conn, {r[1] for r in rows if not r[3]})
    msgs = []
    for content, sender, ts, from_me, media, chat_jid in rows:
        text = (content or "").strip()
        if not text and media:
            text = f"[{media}]"
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + "…"
        msgs.append({"time": _fmt_ts(ts), "from": "עומר" if from_me else names.get(sender, sender),
                     "text": text, "chat": chat_jid})
    return msgs


def read(chat, limit=50, since_hours=None, db_path=None, config_path=kai_bots.CONFIG, device_db=None):
    jid, name = resolve_chat(chat, db_path, config_path)
    limit = max(1, min(int(limit or 50), 300))
    jids = chat_jids(jid, device_db)
    q = ("SELECT content, sender, timestamp, is_from_me, media_type, chat_jid FROM messages WHERE chat_jid IN"
         f" ({','.join('?' * len(jids))}) AND ((content IS NOT NULL AND content != '') OR media_type IS NOT NULL)")
    args = list(jids)
    if since_hours:
        since = datetime.now(timezone.utc) - timedelta(hours=float(since_hours))
        q += " AND datetime(timestamp) >= datetime(?)"
        args.append(since.strftime("%Y-%m-%d %H:%M:%S"))
    q += " ORDER BY datetime(timestamp) DESC LIMIT ?"
    args.append(limit)
    conn = _db(db_path)
    try:
        rows = list(reversed(conn.execute(q, args).fetchall()))
        msgs = _rows_to_msgs(conn, rows)
    finally:
        conn.close()
    for m in msgs:
        m.pop("chat", None)
    return {"chat": name, "jid": jid, "count": len(msgs), "messages": msgs}


def search(query, chat=None, limit=30, db_path=None, config_path=kai_bots.CONFIG, device_db=None):
    if not (query or "").strip():
        raise WaError("empty query")
    q = ("SELECT content, sender, timestamp, is_from_me, media_type, chat_jid FROM messages"
         " WHERE content LIKE ?")
    args = [f"%{query}%"]
    jid = name = None
    if chat:
        jid, name = resolve_chat(chat, db_path, config_path)
        jids = chat_jids(jid, device_db)
        q += f" AND chat_jid IN ({','.join('?' * len(jids))})"
        args.extend(jids)
    q += " ORDER BY datetime(timestamp) DESC LIMIT ?"
    args.append(max(1, min(int(limit or 30), 200)))
    conn = _db(db_path)
    try:
        rows = conn.execute(q, args).fetchall()
        msgs = _rows_to_msgs(conn, rows)
        chat_names = {}
        for m in msgs:
            j = m["chat"]
            if j not in chat_names:
                r = conn.execute("SELECT name FROM chats WHERE jid=?", (j,)).fetchone()
                chat_names[j] = (r[0] if r and r[0] else j)
            m["chat"] = chat_names[j]
    finally:
        conn.close()
    return {"query": query, "chat": name, "count": len(msgs), "messages": msgs}


def chats(query, limit=20, db_path=None):
    conn = _db(db_path)
    try:
        rows = conn.execute("SELECT jid, name, last_message_time FROM chats WHERE name LIKE ?"
                            " ORDER BY last_message_time DESC LIMIT ?", (f"%{query}%", int(limit))).fetchall()
    finally:
        conn.close()
    return [{"jid": j, "name": n, "last": _fmt_ts(t)} for j, n, t in rows]


def with_signature(text):
    text = (text or "").rstrip()
    return text if text.endswith(SIGNATURE) else f"{text}\n{SIGNATURE}"


def send(jid, text, opener=urllib.request.urlopen):
    """Raw bridge send. Callers: bus.py serve ONLY, for a request that carries a hook-written approval."""
    payload = json.dumps({"recipient": jid, "message": with_signature(text)}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{BRIDGE}/api/send", data=payload,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    data = json.loads(opener(req, timeout=15).read())
    if not data.get("success"):
        raise WaError(f"bridge refused: {str(data)[:200]}")
    return {"sent": True, "bridge": str(data.get("message", ""))[:200]}


MEDIA_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def check_media(path):
    """Absolute path of an existing image file, or WaError. Images only (07/11/2026 spec, msg 840)."""
    p = os.path.abspath(str(path or "").strip().strip('"'))
    if os.path.splitext(p)[1].lower() not in MEDIA_EXTS:
        raise WaError(f"not an image ({', '.join(MEDIA_EXTS)}): {path}")
    if not os.path.isfile(p):
        raise WaError(f"file not found: {path}")
    return p


def send_media(jid, path, caption="", sign=True, opener=urllib.request.urlopen):
    """Raw bridge send of ONE image with a caption. Callers: bus.py serve ONLY (approved request).
    The bridge's /api/send takes media_path and sends `message` as the image caption (main.go, Caption)."""
    p = check_media(path)
    caption = (caption or "").strip()
    if sign:
        caption = with_signature(caption).lstrip("\n")
    payload = json.dumps({"recipient": jid, "message": caption, "media_path": p},
                         ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{BRIDGE}/api/send", data=payload,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    data = json.loads(opener(req, timeout=60).read())
    if not data.get("success"):
        raise WaError(f"bridge refused: {str(data)[:200]}")
    return {"sent": True, "file": os.path.basename(p), "bridge": str(data.get("message", ""))[:200]}


def _arg(argv, flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv else default


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    try:
        if cmd == "chats":
            out = chats(_arg(argv, "--query", ""), int(_arg(argv, "--limit", 20)))
        elif cmd == "read":
            out = read(_arg(argv, "--chat"), _arg(argv, "--limit", 50), _arg(argv, "--since-hours"))
        elif cmd == "search":
            out = search(_arg(argv, "--query"), _arg(argv, "--chat"), _arg(argv, "--limit", 30))
        elif cmd == "aliases":
            out = aliases()
        else:
            print(__doc__)
            return 1
    except WaError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.exit(main(sys.argv))
