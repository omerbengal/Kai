#!/usr/bin/env python3
"""
Fast reminder cancellation — cancel a pending reminder by keyword match.
Usage: python fast_reminder_cancel.py --message "keyword" --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *

def main():
    p = base_args("Cancel a pending reminder")
    p.add_argument("--message", required=True, help="Keyword to match reminder message")
    args = p.parse_args()

    start_typing(args.chat_id, args.orig_id)
    try:
        # Look up pending reminder by keyword
        conn = get_db()
        row = conn.execute(
            "SELECT id, message, trigger_at FROM reminders WHERE status='pending' AND message LIKE ? ORDER BY trigger_at ASC LIMIT 1",
            (f"%{args.message}%",)
        ).fetchone()

        if not row:
            conn.close()
            fail(args, f'לא נמצאה תזכורת עם "{args.message}"')
            return

        reminder_id = row["id"]
        reminder_message = row["message"]

        # Soft-cancel: set status='dismissed' (record stays in DB)
        conn.execute("UPDATE reminders SET status='dismissed' WHERE id=?", (reminder_id,))
        conn.commit()

        # Verify
        updated = conn.execute("SELECT status FROM reminders WHERE id=?", (reminder_id,)).fetchone()
        conn.close()

        if updated and updated["status"] == "dismissed":
            action_log("fast_reminder_cancel", "cancelled", f"reminder_id={reminder_id} message={reminder_message}")
            finish(args, f"{RLM}✅ תזכורת בוטלה: {reminder_message}")
        else:
            fail(args, f'ביטול התזכורת "{reminder_message}" נכשל')
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")

if __name__ == "__main__":
    main()
