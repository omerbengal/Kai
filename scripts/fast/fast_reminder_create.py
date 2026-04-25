#!/usr/bin/env python3
"""
Fast reminder creation.
Usage: python fast_reminder_create.py --message "טקסט" --trigger_at "2026-04-13 09:00:00" [--recurrence daily] --ack_id N --orig_id N
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *

def main():
    p = base_args("Create reminder")
    p.add_argument("--message", required=True)
    p.add_argument("--trigger_at", required=True, help="UTC datetime: YYYY-MM-DD HH:MM:SS")
    p.add_argument("--recurrence", default=None, help="daily/weekly/monthly/yearly or None")
    args = p.parse_args()

    start_typing(args.chat_id, args.orig_id)
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO reminders (message, trigger_at, recurrence, status) VALUES (?, ?, ?, 'pending')",
            (args.message, args.trigger_at, args.recurrence if args.recurrence else None)
        )
        conn.commit()
        # Get inserted row
        row = conn.execute("SELECT id, trigger_at FROM reminders ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()

        # Convert UTC to Jerusalem for display
        from datetime import datetime, timezone, timedelta
        JLM = timezone(timedelta(hours=3))
        try:
            dt_utc = datetime.strptime(row["trigger_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            dt_local = dt_utc.astimezone(JLM)
            display_time = dt_local.strftime("%d/%m/%Y %H:%M")
        except Exception:
            display_time = args.trigger_at

        action_log("fast_reminder_create", "created", f'"{args.message}" at {args.trigger_at}')
        finish(args, f'{RLM}✅ תזכורת נוצרה!\n{RLM}📅 {display_time}\n{RLM}💬 {args.message}')
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")

if __name__ == "__main__":
    main()
