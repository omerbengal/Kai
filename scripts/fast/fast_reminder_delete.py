#!/usr/bin/env python3
"""
Fast reminder hard delete — permanently remove a reminder from DB by keyword match.
Usage: python fast_reminder_delete.py --message "keyword" --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *

def main():
    p = base_args("Hard delete a reminder")
    p.add_argument("--message", required=True, help="Keyword to match reminder message")
    args = p.parse_args()

    start_typing(args.chat_id, args.orig_id)
    try:
        conn = get_db()
        # Match any status (pending or dismissed)
        row = conn.execute(
            "SELECT id, message FROM reminders WHERE message LIKE ? ORDER BY trigger_at ASC LIMIT 1",
            (f"%{args.message}%",)
        ).fetchone()

        if not row:
            conn.close()
            fail(args, f'לא נמצאה תזכורת עם "{args.message}"')
            return

        reminder_id = row["id"]
        reminder_message = row["message"]

        conn.execute("DELETE FROM reminders WHERE id=?", (reminder_id,))
        conn.commit()

        gone = conn.execute("SELECT id FROM reminders WHERE id=?", (reminder_id,)).fetchone()
        conn.close()

        if not gone:
            action_log("fast_reminder_delete", "hard_deleted", f"reminder_id={reminder_id} message={reminder_message}")
            finish(args, f"{RLM}✅ תזכורת נמחקה לגמרי: {reminder_message}")
        else:
            fail(args, f'מחיקת התזכורת "{reminder_message}" נכשלה')
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")

if __name__ == "__main__":
    main()
