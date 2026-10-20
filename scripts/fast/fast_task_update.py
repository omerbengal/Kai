#!/usr/bin/env python3
"""
Fast task update — update priority and/or due date of an open task.
Usage: python fast_task_update.py --title "partial" [--priority N] [--due_at "YYYY-MM-DD HH:MM"] --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

--due_at is Omer's LOCAL time (Israel, or the travel-mode destination; --israel_time forces Israel). Converted to UTC before storage.
At least one of --priority or --due_at must be provided.
"""
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
import local_tz  # home timezone (travel-mode aware) - scripts/local_tz.py

JERUSALEM = local_tz.home_tz()  # was fixed UTC+3; travel-mode aware


def format_due(due_str):
    """Convert stored UTC datetime string to Jerusalem display string."""
    if not due_str:
        return '—'
    try:
        if 'T' in due_str:
            dt_utc = datetime.fromisoformat(due_str.replace('Z', '+00:00'))
            if dt_utc.tzinfo is None:
                dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        else:
            dt_utc = datetime.strptime(due_str[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        dt_local = dt_utc.astimezone(JERUSALEM)
        return dt_local.strftime('%d/%m/%Y %H:%M')
    except Exception:
        return due_str[:16]


def main():
    p = base_args("Update task priority or due date")
    p.add_argument("--title", required=True, help="Partial task title to match")
    p.add_argument("--priority", type=int, default=None, help="New priority (0–11)")
    p.add_argument("--due_at", default=None, help="New due date in LOCAL time: YYYY-MM-DD HH:MM")
    p.add_argument("--israel_time", action="store_true", help="Interpret --due_at as Israel time even while abroad")
    args = p.parse_args()

    # At least one update field required
    if args.priority is None and args.due_at is None:
        fail(args, 'יש לספק לפחות שדה אחד לעדכון: --priority או --due_at')
        return

    start_typing(args.chat_id, args.orig_id)
    try:
        # Find open task by partial title
        conn = get_db()
        row = conn.execute(
            "SELECT id, title, priority, due_at FROM tasks WHERE status='open' AND title LIKE ? ORDER BY priority DESC LIMIT 1",
            (f'%{args.title}%',)
        ).fetchone()

        if not row:
            conn.close()
            fail(args, f'לא נמצאה משימה פתוחה עם הכותרת "{args.title}"')
            return

        task_id = row['id']
        task_title = row['title']
        old_priority = row['priority']
        old_due_at = row['due_at']

        # Build SET clause
        set_parts = []
        params = []
        changes = []

        if args.priority is not None:
            set_parts.append('priority = ?')
            params.append(args.priority)
            changes.append(f'{RLM}• עדיפות: {old_priority} ← {args.priority}')

        new_due_utc_str = None
        if args.due_at is not None:
            # Parse as Jerusalem time, convert to UTC
            try:
                dt_local = datetime.strptime(args.due_at.strip(), '%Y-%m-%d %H:%M').replace(tzinfo=local_tz.israel_tz() if args.israel_time else JERUSALEM)
            except ValueError:
                conn.close()
                fail(args, f'פורמט תאריך שגוי: "{args.due_at}" — נדרש YYYY-MM-DD HH:MM')
                return
            dt_utc = dt_local.astimezone(timezone.utc)
            new_due_utc_str = dt_utc.strftime('%Y-%m-%d %H:%M:%S')
            set_parts.append('due_at = ?')
            params.append(new_due_utc_str)
            old_due_display = format_due(old_due_at) if old_due_at else '—'
            new_due_display = local_tz.fmt_local(dt_utc, '%d/%m/%Y %H:%M', force_israel=args.israel_time)
            changes.append(f'{RLM}• תאריך יעד: {old_due_display} ← {new_due_display}')

        params.append(task_id)
        sql = f"UPDATE tasks SET {', '.join(set_parts)} WHERE id = ?"
        conn.execute(sql, params)
        conn.commit()

        # Verify
        updated = conn.execute('SELECT priority, due_at FROM tasks WHERE id = ?', (task_id,)).fetchone()
        conn.close()

        if updated:
            action_log("fast_task_update", "updated", f"task_id={task_id} title={task_title}")
            reply_lines = [f'{RLM}✅ משימה עודכנה: {task_title}'] + changes
            finish(args, '\n'.join(reply_lines), linkify="intl")
        else:
            fail(args, f'עדכון המשימה "{task_title}" נכשל')
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")


if __name__ == "__main__":
    main()
