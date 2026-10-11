#!/usr/bin/env python
"""
Dispatcher opening move in ONE call (step shortening, Omer's request 11/10/2026).

Does what used to be three separate dispatcher steps:
  1. sends the silent "🔄 ⏳" ACK (same route as tg_silent_send.py, saves the `messages` row)
  2. reads tmp/pending_question.json
  3. loads the last 20 messages from kai.db

Usage:
    python D:/Kai/scripts/dispatch_start.py --reply_to ORIGINAL_MSG_ID
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


def main():
    p = argparse.ArgumentParser(description="Dispatcher start: silent ACK + pending question + context")
    p.add_argument("--reply_to", default=None)
    p.add_argument("--text", default="🔄 ⏳")
    p.add_argument("--chat_id", default=os.environ.get("KAI_TELEGRAM_CHAT_ID", ""))
    p.add_argument("--no-ack", action="store_true")
    p.add_argument("--context", type=int, default=20)
    args = p.parse_args()

    if args.no_ack:
        print("ACK_MSG_ID=NONE")
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

    print_pending()
    print_context(args.context)


if __name__ == "__main__":
    main()
