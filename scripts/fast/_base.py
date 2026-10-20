"""
Shared utilities for all fast scripts.
Every fast script imports from this module.
"""
import argparse
import sqlite3
import urllib.request
import json
import sys
import os
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import net_ipv4  # noqa: F401  IPv6 to Telegram is broken here: +10s per call without this

BOT_TOKEN = os.environ["KAI_TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
DB_PATH = "D:/Kai/kai.db"
WHATSAPP_API = "http://localhost:8080"
RLM = "\u200F"

sys.path.insert(0, "D:/Kai/scripts")
try:
    from tg_pin import pin_ack, unpin_ack
except Exception:  # pin support is optional — never break a fast script over it
    def pin_ack(*a, **k):
        pass
    def unpin_ack(*a, **k):
        pass

try:
    import local_tz  # travel-mode aware home timezone; never hard-code UTC+3 (scripts/local_tz.py)
except Exception:
    local_tz = None


def fmt_when(utc_value, fmt="%d/%m %H:%M", force_israel=False):
    """UTC DB value -> local display, e.g. '14/09 12:00 (תאילנד)' while abroad."""
    try:
        return local_tz.fmt_local(utc_value, fmt, force_israel=force_israel)
    except Exception:
        return str(utc_value)[:16]


def base_args(description):
    """Return argparse parser with standard args every script needs.

    Fast-script ACKs are never pinned (Omer, 28/09/2026): they live 3-10s, so a pin
    only flashes the banner. delete_ack() still unpins - a no-op on a non-pinned ACK.
    """
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--ack_id", required=True, help="ACK message ID to delete on completion")
    p.add_argument("--orig_id", required=True, help="Original message ID for reply threading")
    p.add_argument("--chat_id", default=CHAT_ID)
    p.add_argument("--voice_transcription", default=None,
                   help="If set, prepend a voice-echo header (🎤 \"...\") to the final reply. "
                        "Used for voice-initiated requests so the user always sees what was heard.")
    return p

def tg_reply(chat_id, text, reply_to=None, linkify=False):
    """Send a Telegram message and save it to the messages table.

    Returns the telegram_message_id of the sent message, or None on failure.
    Saving to DB is best-effort: if the save fails, the send is still considered successful.
    """
    payload = {"chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_to_message_id"] = int(reply_to)
    send_payloads = [payload]
    if linkify:  # reminders/tasks: phone numbers become tap-to-dial links (scripts/phone_links.py)
        try:
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            import phone_links
            linked = phone_links.telegram_payload(payload)
            if linked is not payload:
                send_payloads = [linked, payload]  # plain text is the fallback if HTML is rejected
        except Exception:
            pass
    msg_id = None
    try:
        last_err = None
        for p in send_payloads:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data=json.dumps(p, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"}
            )
            try:
                resp = urllib.request.urlopen(req, timeout=10)
                result = json.loads(resp.read().decode("utf-8"))
                if result.get("ok"):
                    msg_id = result["result"]["message_id"]
                    break
            except Exception as e:
                last_err = e
        if msg_id is None and last_err is not None:
            raise last_err
    except Exception as e:
        # Last-resort: send plain ASCII error so Omer sees something
        err_payload = json.dumps({"chat_id": chat_id, "text": f"fast-script error: {e}"}).encode()
        err_req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=err_payload,
            headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(err_req, timeout=10)
        except Exception:
            pass
        return None

    # Best-effort: save outgoing message to DB so reactions, bump and history work.
    # Same schema as save_telegram_message.py::save_message.
    if msg_id is not None:
        try:
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).isoformat()
            conn = sqlite3.connect(DB_PATH)
            conn.text_factory = str
            conn.execute(
                "INSERT INTO messages (telegram_message_id, direction, text, reply_to_message_id, reply_to_message_text, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (int(msg_id), "outgoing", text, int(reply_to) if reply_to else None, None, ts)
            )
            conn.commit()
            conn.close()
        except Exception:
            pass  # send already succeeded — DB save is best-effort

    return msg_id

def tg_delete(chat_id, msg_id):
    """Delete a Telegram message and remove from DB."""
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage?chat_id={chat_id}&message_id={msg_id}",
            timeout=5
        )
    except Exception:
        pass
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM messages WHERE telegram_message_id = ?", (int(msg_id),))
        conn.commit()
        conn.close()
    except Exception:
        pass

def start_typing(chat_id, worker_id):
    """NO-OP since investigation #043.

    The typing indicator is no longer per-message. It is owned entirely by the
    hooks in .claude/settings.json via scripts/typing_lease.py: a 15-second
    lease that every tool call renews and that expires on its own when work
    stops. The old per-message typing_pid_{ORIGINAL_MSG_ID} loops were a second,
    blind system that survived the other one's stop and left Omer looking at
    "typing..." for up to 30 minutes.

    The signature is kept so the 23 fast scripts that call it keep working.
    """
    return

def stop_typing(worker_id):
    """NO-OP since investigation #043 — see start_typing above."""
    return

def log_ack(ack_msg_id, action, text=None):
    """Log an ACK message event to the ack_log table."""
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.text_factory = str
        conn.execute(
            "INSERT INTO ack_log (ack_msg_id, action, text, ts) VALUES (?, ?, ?, ?)",
            (int(ack_msg_id), action, text, ts)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

def delete_ack(ack_msg_id, chat_id):
    """Unpin (if pinned), delete an ACK message and log the deletion to ack_log.

    Called from both finish() and fail(), so success and error paths both unpin.
    """
    try:
        unpin_ack(ack_msg_id, chat_id)
    except Exception:
        pass
    tg_delete(chat_id, ack_msg_id)
    log_ack(ack_msg_id, "deleted")

def finish(args, success_text, error_text=None, linkify=False):
    """Stop typing, send final reply, then delete ACK.
    If args.voice_transcription is set, prepend a voice-echo header per worker.md MANDATORY rule."""
    stop_typing(args.orig_id)
    final_text = success_text
    if getattr(args, "voice_transcription", None):
        final_text = f'{RLM}🎤 "{args.voice_transcription}"\n\n{success_text}'
    tg_reply(args.chat_id, final_text, reply_to=args.orig_id, linkify=linkify)
    delete_ack(args.ack_id, args.chat_id)
    print("ok")

def fail(args, error_text):
    """Stop typing, send error reply, then delete ACK.
    If args.voice_transcription is set, prepend a voice-echo header per worker.md MANDATORY rule."""
    stop_typing(args.orig_id)
    body = f"{RLM}❌ {error_text}"
    if getattr(args, "voice_transcription", None):
        body = f'{RLM}🎤 "{args.voice_transcription}"\n\n{body}'
    tg_reply(args.chat_id, body, reply_to=args.orig_id)
    delete_ack(args.ack_id, args.chat_id)
    print(f"fail: {error_text}", file=sys.stderr)

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = str
    conn.row_factory = sqlite3.Row
    return conn

def resolve_name(name):
    """Resolve a person's name using resolve_name.py. Returns dict or None."""
    import subprocess, json
    result = subprocess.run(
        [sys.executable, "D:/Kai/scripts/resolve_name.py", name],
        capture_output=True, text=True, encoding='utf-8'
    )
    if result.returncode == 0:
        try:
            return json.loads(result.stdout.strip())
        except Exception:
            return None
    return None

def find_ambiguous_matches(name):
    """
    Check if a name query could match multiple people in the DB.
    Returns list of (id, name) tuples for all plausible matches.
    A list with >1 entry means ambiguity — the caller should ask for clarification.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = str
    cur = conn.cursor()
    pattern = f"%{name}%"
    # Search by canonical name
    cur.execute("SELECT DISTINCT p.id, p.name FROM people p WHERE p.name LIKE ?", (pattern,))
    by_name = set((r[0], r[1]) for r in cur.fetchall())
    # Search by nickname
    cur.execute("""
        SELECT DISTINCT p.id, p.name FROM people p
        JOIN people_nicknames pn ON p.id = pn.person_id
        WHERE pn.nickname LIKE ?
    """, (pattern,))
    by_nick = set((r[0], r[1]) for r in cur.fetchall())
    conn.close()
    all_matches = list(by_name | by_nick)
    return all_matches

WA_DB = "D:/Kai/whatsapp-mcp/whatsapp-bridge/store/messages.db"
WA_DEVICE_DB = "D:/Kai/whatsapp-mcp/whatsapp-bridge/store/whatsapp.db"


def _save_wa_jid(person_id, phone, jid):
    """Cache a resolved WhatsApp JID back to people or phone_contacts."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.text_factory = str
        if person_id is not None:
            conn.execute("UPDATE people SET wa_jid = ? WHERE id = ?", (jid, person_id))
        elif phone:
            conn.execute("UPDATE phone_contacts SET wa_jid = ? WHERE phone = ?", (jid, phone))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _resolve_jid_from_phone(phone, raw_phone, person_id, display):
    """
    Given a phone number, try <email> then LID lookup.
    Returns (jid, display) on success or None.
    Caches the found JID.
    """
    if not phone:
        return None

    jid_phone = f"{phone}@s.whatsapp.net"
    wa = sqlite3.connect(WA_DB)
    row = wa.execute("SELECT jid FROM chats WHERE jid = ?", (jid_phone,)).fetchone()
    wa.close()
    if row:
        _save_wa_jid(person_id, raw_phone, jid_phone)
        return jid_phone, display

    try:
        dev = sqlite3.connect(WA_DEVICE_DB)
        lid_row = dev.execute("SELECT lid FROM whatsmeow_lid_map WHERE pn = ?", (phone,)).fetchone()
        dev.close()
        if lid_row:
            jid_lid = f"{lid_row[0]}@lid"
            wa = sqlite3.connect(WA_DB)
            chat_row = wa.execute("SELECT jid FROM chats WHERE jid = ?", (jid_lid,)).fetchone()
            wa.close()
            if chat_row:
                _save_wa_jid(person_id, raw_phone, jid_lid)
                return jid_lid, display
    except Exception:
        pass

    # Fallback: construct JID from phone even without existing chat.
    # WhatsApp MCP can initiate new conversations with a bare number JID.
    return f"{phone}@s.whatsapp.net", display


def find_wa_chat_jid(name):
    """
    Resolve a person name to a WhatsApp chat JID.
    Returns (jid, display_name) or (None, error_info).

    Priority:
    1. people table (has top priority over phone_contacts)
       a. Ambiguity check in people
       b. Cached wa_jid in people → validate → return
       c. Fuzzy match in people → phone/LID resolution → cache & return
    2. phone_contacts (only if name not found in people at all)
       a. LIKE search → ambiguity check
       b. Cached wa_jid in phone_contacts → validate → return
       c. Phone/LID resolution → cache & return
    3. chats.name LIKE search (final fallback)
    """
    # ── Step 1: people table ──────────────────────────────────────────────
    matches = find_ambiguous_matches(name)  # searches people + people_nicknames
    if len(matches) > 1:
        return None, ("ambiguous", [m[1] for m in matches])

    if matches:
        # Exactly one person in people table
        person_id = matches[0][0]
        conn = sqlite3.connect(DB_PATH)
        conn.text_factory = str
        prow = conn.execute(
            "SELECT name, phone, wa_jid FROM people WHERE id = ?", (person_id,)
        ).fetchone()
        conn.close()
        if prow:
            display, raw_phone, cached_jid = prow[0], prow[1], prow[2]
            phone = str(raw_phone).replace("+", "").replace("-", "").replace(" ", "") if raw_phone else None

            # 1a: cached JID
            if cached_jid:
                wa = sqlite3.connect(WA_DB)
                valid = wa.execute("SELECT jid FROM chats WHERE jid = ?", (cached_jid,)).fetchone()
                wa.close()
                if valid:
                    return cached_jid, display

            # 1b: phone/LID resolution
            result = _resolve_jid_from_phone(phone, raw_phone, person_id, display)
            if result:
                return result

        # Found in people but couldn't resolve WhatsApp — do NOT fall through to phone_contacts
        # Try chats.name as last resort
        wa = sqlite3.connect(WA_DB)
        rows = wa.execute(
            "SELECT jid, name FROM chats WHERE name LIKE ? ORDER BY last_message_time DESC LIMIT 5",
            (f"%{name}%",)
        ).fetchall()
        wa.close()
        if len(rows) == 1:
            _save_wa_jid(person_id, None, rows[0][0])
            return rows[0][0], rows[0][1]
        if len(rows) > 1:
            return None, ("ambiguous", [r[1] for r in rows])
        return None, ("not_found", name)

    # ── Step 2: phone_contacts (only reached if name not in people) ───────
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = str
    pc_rows = conn.execute(
        "SELECT name, phone, wa_jid FROM phone_contacts WHERE name LIKE ? ORDER BY name ASC LIMIT 10",
        (f"%{name}%",)
    ).fetchall()
    conn.close()

    if len(pc_rows) > 1:
        return None, ("ambiguous", [r[0] for r in pc_rows])

    if len(pc_rows) == 1:
        pc_name, raw_phone, cached_jid = pc_rows[0]
        phone = str(raw_phone).replace("+", "").replace("-", "").replace(" ", "") if raw_phone else None

        # 2a: cached JID
        if cached_jid:
            wa = sqlite3.connect(WA_DB)
            valid = wa.execute("SELECT jid FROM chats WHERE jid = ?", (cached_jid,)).fetchone()
            wa.close()
            if valid:
                return cached_jid, pc_name

        # 2b: phone/LID resolution
        result = _resolve_jid_from_phone(phone, raw_phone, None, pc_name)
        if result:
            return result

    # ── Step 3: chats.name LIKE (final fallback) ──────────────────────────
    wa = sqlite3.connect(WA_DB)
    rows = wa.execute(
        "SELECT jid, name FROM chats WHERE name LIKE ? ORDER BY last_message_time DESC LIMIT 5",
        (f"%{name}%",)
    ).fetchall()
    wa.close()
    if len(rows) == 1:
        return rows[0][0], rows[0][1]
    if len(rows) > 1:
        return None, ("ambiguous", [r[1] for r in rows])

    return None, ("not_found", name)


def whatsapp_send(recipient, message):
    """Send a WhatsApp message. recipient can be a phone number or full JID (with @). Returns True on success."""
    payload = json.dumps({"recipient": recipient, "message": message}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{WHATSAPP_API}/api/send",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"}
    )
    resp = urllib.request.urlopen(req, timeout=10).read()
    data = json.loads(resp)
    return data.get("success", False)

def whatsapp_send_file(recipient, file_path, caption=None):
    """
    Send a file (image/audio/document) via WhatsApp.
    file_path: absolute path to the file on disk.
    caption: optional text caption (appended to message field).
    Returns True on success.

    The bridge determines media type automatically from the file extension:
      images  → jpg, jpeg, png, gif, webp
      audio   → mp3, ogg, opus, m4a, aac, wav
      video   → mp4, mov, avi, mkv
      other   → sent as document
    """
    body = {"recipient": recipient, "media_path": file_path}
    if caption:
        body["message"] = caption
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{WHATSAPP_API}/api/send",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"}
    )
    resp = urllib.request.urlopen(req, timeout=30).read()
    data = json.loads(resp)
    return data.get("success", False)

def action_log(agent, action, details):
    """Write to action_log table."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.text_factory = str
        conn.execute(
            "INSERT INTO action_log (agent, action, details) VALUES (?, ?, ?)",
            (agent, action, details)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
