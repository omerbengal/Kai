#!/usr/bin/env python3
"""
Fast reminder creation.
Usage: python fast_reminder_create.py --message "טקסט" --local_at "2026-04-13 12:00" [--israel_time] [--recurrence daily] --ack_id N --orig_id N
       (legacy) --trigger_at "2026-04-13 09:00:00"  # already UTC
--local_at is Omer's LOCAL wall-clock time (Israel, or the travel-mode destination);
--israel_time forces Israel time while abroad ("שעון ישראל").
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
import local_tz  # home timezone (travel-mode aware) - scripts/local_tz.py

def main():
    p = base_args("Create reminder")
    p.add_argument("--message", required=True)
    p.add_argument("--trigger_at", default=None, help="UTC datetime: YYYY-MM-DD HH:MM:SS (legacy; prefer --local_at)")
    p.add_argument("--local_at", default=None, help="LOCAL time 'YYYY-MM-DD HH:MM' (home tz / travel destination)")
    p.add_argument("--israel_time", action="store_true", help="Interpret --local_at as Israel time even while abroad")
    p.add_argument("--recurrence", default=None, help="daily/weekly/monthly/yearly or None")
    args = p.parse_args()

    start_typing(args.chat_id, args.orig_id)
    try:
        # Normalize trigger_at to canonical UTC "YYYY-MM-DD HH:MM:SS".
        # The heartbeat compares trigger_at as a STRING; an ISO "T...Z" value sorts
        # after the space-format "now" and would never fire. Always store space format.
        if args.local_at:
            trigger_at = local_tz.local_str_to_utc_str(args.local_at, force_israel=args.israel_time)
        elif args.trigger_at:
            trigger_at = args.trigger_at.strip().replace("T", " ").replace("Z", "")[:19]
        else:
            fail(args, "חסר זמן - יש לספק --local_at או --trigger_at")
            return

        conn = get_db()
        conn.execute(
            "INSERT INTO reminders (message, trigger_at, recurrence, status) VALUES (?, ?, ?, 'pending')",
            (args.message, trigger_at, args.recurrence if args.recurrence else None)
        )
        conn.commit()
        # Get inserted row
        row = conn.execute("SELECT id, trigger_at FROM reminders ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()

        # UTC -> local display ("12:00 (תאילנד)" while abroad)
        try:
            display_time = local_tz.fmt_local(row["trigger_at"], "%d/%m/%Y %H:%M", force_israel=args.israel_time)
        except Exception:
            display_time = trigger_at

        action_log("fast_reminder_create", "created", f'"{args.message}" at {trigger_at} UTC')
        finish(args, f'{RLM}✅ תזכורת נוצרה!\n{RLM}📅 {display_time}\n{RLM}💬 {args.message}', linkify=True)
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")

if __name__ == "__main__":
    main()
