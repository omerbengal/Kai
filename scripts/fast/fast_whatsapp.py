#!/usr/bin/env python3
"""
Fast WhatsApp send to individual contact.
Supports text, images, audio, and files.

Usage:
  # Text only
  python fast_whatsapp.py --to "שם" --message "טקסט" --ack_id N --orig_id N

  # File only (no caption)
  python fast_whatsapp.py --to "שם" --file "/abs/path/img.jpg" --ack_id N --orig_id N

  # File with caption
  python fast_whatsapp.py --to "שם" --file "/abs/path/img.jpg" --message "כיתוב" --ack_id N --orig_id N

At least one of --message or --file must be provided.
"""
import sys
import os
import hashlib
import sqlite3
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *

SUFFIX = "\n[נשלח על ידי קאי 🤖]"
DEDUP_WINDOW_SECONDS = 60

def main():
    p = base_args("Send WhatsApp to individual")
    p.add_argument("--to", required=True, help="Contact name")
    p.add_argument("--message", default=None, help="Message text (optional when --file is given)")
    p.add_argument("--file", default=None, dest="file_path",
                   help="Absolute path to file (image/audio/document) to send")
    args = p.parse_args()

    # Validate: at least one of --message or --file
    if not args.message and not args.file_path:
        p.error("At least one of --message or --file must be provided.")

    # Validate file exists
    if args.file_path and not os.path.isfile(args.file_path):
        p.error(f"File not found: {args.file_path}")

    # Dedup guard: skip if the same (recipient, message, file) was sent in the last 60s.
    # Protects against accidental double-sends (e.g. caller re-ran after mis-reading silent success).
    dedup_key = hashlib.md5(
        f"{args.to}|{args.message or ''}|{args.file_path or ''}".encode("utf-8")
    ).hexdigest()[:12]
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.text_factory = str
        recent = conn.execute(
            """SELECT id FROM action_log
               WHERE agent='fast_whatsapp'
                 AND action IN ('sent', 'sent_file')
                 AND details LIKE ?
                 AND created_at >= datetime('now', ?)
               LIMIT 1""",
            (f"%dedup={dedup_key}%", f"-{DEDUP_WINDOW_SECONDS} seconds"),
        ).fetchone()
        conn.close()
    except Exception:
        recent = None
    if recent:
        action_log("fast_whatsapp", "dedup_skip", f"to={args.to} dedup={dedup_key}")
        print(f"dedup: skipped duplicate to {args.to} (within {DEDUP_WINDOW_SECONDS}s)")
        return

    start_typing(args.chat_id, args.orig_id)
    try:
        jid, info = find_wa_chat_jid(args.to)

        if jid is None:
            kind, data = info
            if kind == "ambiguous":
                names_list = "\n".join(f"{RLM}• {n}" for n in sorted(data))
                fail(args, f'מצאתי כמה אפשרויות בשם "{args.to}":\n{names_list}\n{RLM}אנא פרט את השם המלא.')
            else:
                fail(args, f'לא נמצאה שיחת וואטסאפ עם "{args.to}"')
            return

        display_name = info

        if args.file_path:
            # Send file (with optional caption)
            caption = (args.message + SUFFIX) if args.message else None
            success = whatsapp_send_file(jid, args.file_path, caption=caption)
            file_name = os.path.basename(args.file_path)
            if success:
                action_log("fast_whatsapp", "sent_file", f"to={display_name} jid={jid} file={file_name} dedup={dedup_key}")
                if args.message:
                    finish(args, f'{RLM}✅ שלחתי ל-{display_name} בוואטסאפ:\n{RLM}📎 {file_name}\n{RLM}"{args.message}"')
                else:
                    finish(args, f'{RLM}✅ שלחתי ל-{display_name} בוואטסאפ:\n{RLM}📎 {file_name}')
            else:
                fail(args, f'שליחת הקובץ ל-{display_name} נכשלה')
        else:
            # Text only (existing behavior)
            full_message = args.message + SUFFIX
            success = whatsapp_send(jid, full_message)
            if success:
                action_log("fast_whatsapp", "sent", f"to={display_name} jid={jid} dedup={dedup_key}")
                finish(args, f'{RLM}✅ שלחתי ל-{display_name} בוואטסאפ:\n{RLM}"{args.message}"')
            else:
                fail(args, f'שליחת וואטסאפ ל-{display_name} נכשלה')

    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")

if __name__ == "__main__":
    main()
