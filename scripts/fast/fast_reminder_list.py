#!/usr/bin/env python3
"""
Fast reminder list — show pending reminders.
Usage: python fast_reminder_list.py --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID} [--days 30]
"""
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
import local_tz  # home timezone (travel-mode aware) - scripts/local_tz.py

JERUSALEM = local_tz.home_tz()  # was fixed UTC+3; travel-mode aware

RECURRENCE_LABELS = {
    'daily':   '🔁 יומי',
    'weekly':  '🔁 שבועי',
    'monthly': '🔁 חודשי',
    'yearly':  '🔁 שנתי',
}


def main():
    p = base_args("List pending reminders")
    p.add_argument("--days", type=int, default=30, help="Show reminders within next N days (default: 30)")
    args = p.parse_args()

    start_typing(args.chat_id, args.orig_id)
    try:
        conn = get_db()
        # Calculate upper bound
        now_utc = datetime.now(timezone.utc)
        cutoff_utc = now_utc + timedelta(days=args.days)
        cutoff_str = cutoff_utc.strftime('%Y-%m-%d %H:%M:%S')

        rows = conn.execute(
            """SELECT id, message, trigger_at, recurrence
               FROM reminders
               WHERE status = 'pending'
                 AND trigger_at >= datetime('now')
                 AND trigger_at <= ?
               ORDER BY trigger_at ASC
               LIMIT 20""",
            (cutoff_str,)
        ).fetchall()
        conn.close()

        if not rows:
            finish(args, f'{RLM}אין תזכורות קרובות ב-{args.days} הימים הקרובים')
            return

        lbl = local_tz.tz_label()
        lines = [f'{RLM}⏰ תזכורות קרובות ({len(rows)})' + (f' - שעון {lbl}' if lbl else '') + ':']
        for row in rows:
            # Parse trigger_at — may be "YYYY-MM-DD HH:MM:SS" or ISO format
            trigger_str = row['trigger_at']
            try:
                if 'T' in trigger_str:
                    dt_utc = datetime.fromisoformat(trigger_str.replace('Z', '+00:00'))
                    if dt_utc.tzinfo is None:
                        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
                else:
                    dt_utc = datetime.strptime(trigger_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                dt_local = dt_utc.astimezone(JERUSALEM)
                time_str = dt_local.strftime('%d/%m %H:%M')
            except Exception:
                time_str = trigger_str[:16]

            rec = row['recurrence']
            rec_label = f' [{RECURRENCE_LABELS.get(rec, f"🔁 {rec}")}]' if rec else ''
            lines.append(f'{RLM}• {time_str} — {row["message"]}{rec_label}')

        finish(args, '\n'.join(lines), linkify=True)
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")


if __name__ == "__main__":
    main()
