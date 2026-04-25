#!/usr/bin/env python3
"""
Fast task completion — mark a task as done by partial title match.
Usage: python fast_task_done.py --title "partial title" --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}
       Optional: --completed_at "YYYY-MM-DD HH:MM" (Jerusalem time, UTC+3) to set a custom completion timestamp
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
from datetime import datetime, timezone, timedelta

def main():
    p = base_args("Mark a task as done")
    p.add_argument("--title", required=True, help="Partial task title to match")
    p.add_argument("--completed_at", default=None, help='Custom completion time in Jerusalem time, format: "YYYY-MM-DD HH:MM"')
    args = p.parse_args()

    # Resolve completed_at timestamp
    if args.completed_at:
        JLM = timezone(timedelta(hours=3))
        dt = datetime.strptime(args.completed_at, '%Y-%m-%d %H:%M').replace(tzinfo=JLM)
        completed_at_utc = dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        completed_at_sql = f"'{completed_at_utc}'"
    else:
        completed_at_sql = "datetime('now')"

    start_typing(args.chat_id, args.orig_id)
    try:
        # Look up open task by partial title
        conn = get_db()
        row = conn.execute(
            "SELECT id, title FROM tasks WHERE status='open' AND title LIKE ? ORDER BY priority DESC LIMIT 1",
            (f"%{args.title}%",)
        ).fetchone()

        if not row:
            conn.close()
            fail(args, f'לא נמצאה משימה עם הכותרת "{args.title}"')
            return

        task_id = row["id"]
        task_title = row["title"]

        # Mark as done
        conn.execute(f"UPDATE tasks SET status='done', completed_at={completed_at_sql} WHERE id=?", (task_id,))
        conn.commit()

        # Verify
        updated = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        conn.close()

        if updated and updated["status"] == "done":
            action_log("fast_task_done", "completed", f"task_id={task_id} title={task_title}")
            finish(args, f"{RLM}✅ משימה הושלמה: {task_title}")
        else:
            fail(args, f'עדכון המשימה "{task_title}" נכשל')
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")

if __name__ == "__main__":
    main()
