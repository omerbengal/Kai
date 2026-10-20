#!/usr/bin/env python3
"""
Fast reminder update — change time and/or message text of a pending reminder.
At least one of --trigger_at or --new_message must be provided.

Usage:
  # Reschedule only:
  python fast_reminder_update.py --message "keyword" --trigger_at "YYYY-MM-DD HH:MM:SS" --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

  # Rename only:
  python fast_reminder_update.py --message "keyword" --new_message "new text" --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

  # Both:
  python fast_reminder_update.py --message "keyword" --new_message "new text" --trigger_at "YYYY-MM-DD HH:MM:SS" --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

Time: prefer --local_at 'YYYY-MM-DD HH:MM' (Omer's LOCAL time; --israel_time forces Israel). --trigger_at is UTC.
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
import local_tz  # home timezone (travel-mode aware) - scripts/local_tz.py
from datetime import datetime, timezone, timedelta


def main():
    p = base_args("Update a pending reminder (time and/or message text)")
    p.add_argument("--message", required=True, help="Keyword to match in existing reminder message")
    p.add_argument("--trigger_at", default=None,
                   help="New time in UTC: 'YYYY-MM-DD HH:MM:SS' (legacy; prefer --local_at)")
    p.add_argument("--local_at", default=None, help="New LOCAL time 'YYYY-MM-DD HH:MM' (home tz / travel destination)")
    p.add_argument("--israel_time", action="store_true", help="Interpret --local_at as Israel time even while abroad")
    p.add_argument("--new_message", default=None,
                   help="New reminder message text (replaces existing text)")
    args = p.parse_args()
    if args.local_at:
        try:
            args.trigger_at = local_tz.local_str_to_utc_str(args.local_at, force_israel=args.israel_time)
        except ValueError as e:
            fail(args, f"פורמט זמן שגוי: {e}")
            return

    if not args.trigger_at and not args.new_message:
        # Should not happen if dispatcher is correct, but guard anyway
        # Parse args first, then we can call fail
        pass

    start_typing(args.chat_id, args.orig_id)

    if not args.trigger_at and not args.new_message:
        fail(args, "חסר פרמטר — יש לספק --trigger_at ו/או --new_message")
        return

    try:
        conn = get_db()

        # Find the reminder
        row = conn.execute(
            "SELECT id, message, trigger_at FROM reminders WHERE status='pending' AND message LIKE ? ORDER BY trigger_at ASC LIMIT 1",
            (f"%{args.message}%",)
        ).fetchone()

        if not row:
            fail(args, f'לא נמצאה תזכורת עם "{args.message}"')
            conn.close()
            return

        reminder_id = row["id"]
        old_message = row["message"]
        old_utc = row["trigger_at"]

        # Build update
        updates = []
        params = []

        if args.new_message:
            updates.append("message = ?")
            params.append(args.new_message)

        if args.trigger_at:
            updates.append("trigger_at = ?")
            params.append(args.trigger_at)

        params.append(reminder_id)
        conn.execute(f"UPDATE reminders SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        conn.close()

        # Build confirmation text
        final_message = args.new_message if args.new_message else old_message

        if args.trigger_at:
            try:
                dt_utc = datetime.strptime(args.trigger_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                time_display = local_tz.fmt_local(dt_utc, "%d/%m %H:%M", force_israel=args.israel_time)
            except Exception:
                time_display = args.trigger_at
        else:
            # Keep old time, display it
            try:
                dt_utc = datetime.strptime(old_utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                time_display = local_tz.fmt_local(dt_utc, "%d/%m %H:%M", force_israel=args.israel_time)
            except Exception:
                time_display = old_utc

        action_log("fast_reminder_update", "updated", f"id={reminder_id} msg_changed={bool(args.new_message)} time_changed={bool(args.trigger_at)}")
        finish(args, f"{RLM}✅ תזכורת עודכנה:\n{RLM}📌 {final_message}\n{RLM}📅 {time_display}", linkify="intl")
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")


if __name__ == "__main__":
    main()
