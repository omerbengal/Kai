#!/usr/bin/env python3
"""
Bump (quote-reply with ".") a message from the Telegram history.
Searches the messages DB by keyword, finds the best match, and replies to it.

Usage:
  python fast_bump.py --keyword "[text]" [--direction outgoing|incoming] --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

--direction: filter by message direction (default: both). Use 'outgoing' to find messages Kai sent.
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *


def main():
    p = base_args("Bump a message")
    p.add_argument("--keyword", required=True, help="Text to search in message history")
    p.add_argument("--direction", default=None, choices=["incoming", "outgoing"],
                   help="Filter by direction: 'outgoing' = Kai's messages, 'incoming' = Omer's messages")
    args = p.parse_args()

    start_typing(args.chat_id, args.orig_id)
    try:
        conn = get_db()

        if args.direction:
            rows = conn.execute(
                "SELECT telegram_message_id, text FROM messages "
                "WHERE text LIKE ? AND direction = ? AND telegram_message_id IS NOT NULL "
                "ORDER BY id DESC LIMIT 5",
                (f"%{args.keyword}%", args.direction)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT telegram_message_id, text FROM messages "
                "WHERE text LIKE ? AND telegram_message_id IS NOT NULL "
                "ORDER BY id DESC LIMIT 5",
                (f"%{args.keyword}%",)
            ).fetchall()
        conn.close()

        if not rows:
            fail(args, f'לא נמצאה הודעה עם "{args.keyword}"'
                       + (f' (כיוון: {args.direction})' if args.direction else ''))
            return

        target_msg_id = rows[0]["telegram_message_id"]

        # Reply "." to the target message — this IS the confirmation, no extra message needed
        tg_reply(args.chat_id, ".", reply_to=target_msg_id)

        action_log("fast_bump", "bumped", f'keyword="{args.keyword}" target_msg={target_msg_id}')

        # Delete ACK only — no final confirmation message
        stop_typing(args.orig_id)
        tg_delete(args.chat_id, args.ack_id)

    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")


if __name__ == "__main__":
    main()
