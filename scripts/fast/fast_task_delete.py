#!/usr/bin/env python3
"""
Fast task hard delete — permanently remove a task from DB by partial title match.
Usage: python fast_task_delete.py --title "keyword" --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}
       Optional: --domain <domain> to restrict match to a specific domain (work/personal/etc.)
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *

def main():
    p = base_args("Hard delete a task")
    p.add_argument("--title", required=True, help="Keyword to match task title")
    p.add_argument("--domain", default=None, help="Optional domain filter (work/personal/etc.)")
    args = p.parse_args()

    start_typing(args.chat_id, args.orig_id)
    try:
        conn = get_db()
        if args.domain:
            row = conn.execute(
                """SELECT t.id, t.title FROM tasks t
                   LEFT JOIN domains d ON t.domain_id = d.id
                   WHERE t.title LIKE ? AND d.name = ?
                   ORDER BY t.status='open' DESC, t.priority DESC LIMIT 1""",
                (f"%{args.title}%", args.domain)
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT id, title FROM tasks
                   WHERE title LIKE ?
                   ORDER BY status='open' DESC, priority DESC LIMIT 1""",
                (f"%{args.title}%",)
            ).fetchone()

        if not row:
            conn.close()
            scope = f' בתחום {args.domain}' if args.domain else ''
            fail(args, f'לא נמצאה משימה{scope} עם "{args.title}"')
            return

        task_id = row["id"]
        task_title = row["title"]

        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        conn.commit()

        gone = conn.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone()
        conn.close()

        if not gone:
            action_log("fast_task_delete", "hard_deleted", f"task_id={task_id} title={task_title}")
            finish(args, f"{RLM}✅ משימה נמחקה לגמרי: {task_title}")
        else:
            fail(args, f'מחיקת המשימה "{task_title}" נכשלה')
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")

if __name__ == "__main__":
    main()
