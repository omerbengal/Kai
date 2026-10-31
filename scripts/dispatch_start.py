#!/usr/bin/env python
"""
Dispatcher opening move in ONE call (step shortening, Omer's request 11/10/2026).

Does what used to be three separate dispatcher steps:
  1. sends the silent "🔄 ⏳" ACK (same route as tg_silent_send.py, saves the `messages` row)
  2. reads tmp/pending_question.json
  3. loads the last 20 messages from kai.db

Usage:
    python D:/Kai/scripts/dispatch_start.py --reply_to ORIGINAL_MSG_ID
    python D:/Kai/scripts/dispatch_start.py --reply_to ORIGINAL_MSG_ID --title "<summary>" --pin
        # worker path in ONE call (spawn speedup, 19/10/2026): the ACK goes out already
        # titled "🔄 <summary> — ⏳ מייצר agent...", is logged to ack_log and pinned.
    python D:/Kai/scripts/dispatch_start.py --ack_id ID --title "<summary>" --pin
        # an ACK already exists (voice "🎤 מתמלל..." / Option D turned worker): edit it to the
        # titled text instead of sending a new one, log it, pin it. Prints ACK_MSG_ID=<ID>.
    python D:/Kai/scripts/dispatch_start.py --ack_id ID --title "<summary>" --queued --pin
        # follow-up by SendMessage to an agent that already exists: nothing is being spawned, and a busy
        # agent reads the message only at its next tool round (15 min in the worst case), so the ACK says
        # "🔄 <summary> — 📋 בתור אצל ה-agent שכבר עובד על זה" instead of "מייצר agent" (Omer msg 21171).
    python D:/Kai/scripts/dispatch_start.py --no-ack          # trivial / voice: pending + context only

Output (stdout):
    ACK_MSG_ID=<id>            (or ACK_MSG_ID=FAILED -> fall back to the reply tool; or NONE with --no-ack)
    PENDING=none               (or PENDING=<n> followed by one "  msg_id=..: question" line each)
    CONTEXT:
    [ts] direction: text[:120]   x20

Never touches the typing indicator (investigation #043). Never exits non-zero for an ACK failure:
the pending/context part must still be printed so the dispatcher can carry on with the fallback.
"""
import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tg_silent_send
import tg_pin

BOT_TOKEN = tg_silent_send.BOT_TOKEN

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PQ_PATH = "D:/Kai/tmp/pending_question.json"
DB_PATH = "D:/Kai/kai.db"


def print_pending():
    pending = []
    try:
        if os.path.exists(PQ_PATH):
            with open(PQ_PATH, "r", encoding="utf-8") as f:
                pending = json.load(f)
            if isinstance(pending, dict):  # backwards compat: single object
                pending = [pending]
    except Exception as e:
        print(f"PENDING=error {e}")
        return
    if not pending:
        print("PENDING=none")
        return
    print(f"PENDING={len(pending)}")
    for q in pending:
        print(f"  msg_id={q.get('asked_at_msg_id')}: {q.get('question')}")


def print_context(limit):
    print("CONTEXT:")
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.text_factory = str
        conn.row_factory = sqlite3.Row
        rows = list(reversed(conn.execute(
            "SELECT direction, text, ts FROM messages ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()))
        conn.close()
        for r in rows:
            print(f"[{r['ts']}] {r['direction']}: {(r['text'] or '')[:120]}")
    except Exception as e:
        print(f"  context error: {e}")


def edit_text(chat_id, message_id, text):
    import json as _json
    import urllib.request
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
        data=_json.dumps({"chat_id": chat_id, "message_id": int(message_id), "text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = _json.loads(resp.read().decode("utf-8"))
    if not data.get("ok") and "not modified" not in str(data.get("description", "")):
        raise RuntimeError(f"Telegram API error: {data}")


def log_ack(ack_id, action, text):
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("INSERT INTO ack_log (ack_msg_id, action, text, ts) VALUES (?, ?, ?, ?)",
                 (int(ack_id), action, text, ts))
    conn.commit()
    conn.close()


def main():
    p = argparse.ArgumentParser(description="Dispatcher start: silent ACK + pending question + context")
    p.add_argument("--reply_to", default=None)
    p.add_argument("--text", default="🔄 ⏳")
    p.add_argument("--chat_id", default=os.environ.get("KAI_TELEGRAM_CHAT_ID", ""))
    p.add_argument("--no-ack", action="store_true")
    p.add_argument("--context", type=int, default=20)
    p.add_argument("--title", default=None,
                   help='worker path: ACK text becomes "🔄 <title> — ⏳ מייצר agent..." and is logged to ack_log')
    p.add_argument("--queued", action="store_true",
                   help='with --title: follow-up to an existing agent, ACK says "בתור" and never "מייצר agent"')
    p.add_argument("--pin", action="store_true", help="pin the ACK (worker-spawn points ONLY)")
    p.add_argument("--ack_id", default=None, help="edit this existing ACK instead of sending a new one")
    p.add_argument("--no-context", action="store_true", help="skip PENDING/CONTEXT output (ACK-only call)")
    args = p.parse_args()

    if args.title:
        status = "📋 בתור אצל ה-agent שכבר עובד על זה" if args.queued else "⏳ מייצר agent..."
        args.text = f"🔄 {args.title} — {status}"

    mid = None
    if args.no_ack:
        print("ACK_MSG_ID=NONE")
    elif args.ack_id:
        mid = int(args.ack_id)
        try:
            edit_text(args.chat_id, mid, args.text)
        except Exception as e:
            print(f"dispatch_start: ACK edit failed: {e}", file=sys.stderr)
        print(f"ACK_MSG_ID={mid}")
    else:
        try:
            mid = tg_silent_send.send(args.chat_id, args.text, args.reply_to)
            try:
                tg_silent_send.save(mid, args.text)
            except Exception as e:
                print(f"dispatch_start: DB save failed: {e}", file=sys.stderr)
            print(f"ACK_MSG_ID={mid}")
        except Exception as e:
            print(f"dispatch_start: ACK failed: {e}", file=sys.stderr)
            print("ACK_MSG_ID=FAILED")
            mid = None

    if mid and args.title:
        try:
            log_ack(mid, "sent", args.text)
        except Exception as e:
            print(f"dispatch_start: ack_log failed: {e}", file=sys.stderr)
    if mid and args.pin:
        tg_pin.pin_ack(mid, args.chat_id)  # best-effort, never raises

    if args.no_context:
        return
    print_pending()
    print_context(args.context)


if __name__ == "__main__":
    main()
