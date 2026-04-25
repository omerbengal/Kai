#!/usr/bin/env python3
"""
Fast task list query.
Usage: python fast_tasks_list.py [--filter today|all|overdue|domain|priority] [--domain "work"] [--priority 10] --ack_id N --orig_id N
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
from datetime import datetime, timezone, timedelta

JLM = timezone(timedelta(hours=3))

def main():
    p = base_args("List tasks")
    p.add_argument("--filter", default="all", choices=["today", "all", "overdue", "domain", "priority"])
    p.add_argument("--domain", default=None)
    p.add_argument("--priority", type=int, default=None)
    args = p.parse_args()

    # Auto-detect filter from provided args
    if args.domain and args.filter == "all":
        args.filter = "domain"
    if args.priority is not None and args.filter == "all":
        args.filter = "priority"

    start_typing(args.chat_id, args.orig_id)
    try:
        conn = get_db()
        now = datetime.now(JLM)

        if args.filter == "today":
            start = now.replace(hour=0, minute=0, second=0).astimezone(timezone.utc).isoformat()
            end = now.replace(hour=23, minute=59, second=59).astimezone(timezone.utc).isoformat()
            rows = conn.execute("""
                SELECT t.title, t.priority, d.name as domain, t.due_at
                FROM tasks t LEFT JOIN domains d ON t.domain_id = d.id
                WHERE t.status = 'open' AND t.due_at BETWEEN ? AND ?
                ORDER BY t.priority DESC
            """, (start, end)).fetchall()
            header = "📋 משימות להיום"

        elif args.filter == "overdue":
            rows = conn.execute("""
                SELECT t.title, t.priority, d.name as domain, t.due_at
                FROM tasks t LEFT JOIN domains d ON t.domain_id = d.id
                WHERE t.status = 'open' AND t.due_at < datetime('now')
                ORDER BY t.priority DESC
            """).fetchall()
            header = "⚠️ משימות באיחור"

        elif args.filter == "domain" and args.domain:
            rows = conn.execute("""
                SELECT t.title, t.priority, d.name as domain, t.due_at
                FROM tasks t LEFT JOIN domains d ON t.domain_id = d.id
                WHERE t.status = 'open' AND lower(d.name) = lower(?)
                ORDER BY t.priority DESC
            """, (args.domain,)).fetchall()
            header = f"📋 משימות — {args.domain}"

        elif args.filter == "priority" and args.priority is not None:
            rows = conn.execute("""
                SELECT t.title, t.priority, d.name as domain, t.due_at
                FROM tasks t LEFT JOIN domains d ON t.domain_id = d.id
                WHERE t.status = 'open' AND t.priority >= ?
                ORDER BY t.priority DESC
            """, (args.priority,)).fetchall()
            header = f"📋 משימות עדיפות {args.priority}+"

        else:  # all
            rows = conn.execute("""
                SELECT t.title, t.priority, d.name as domain, t.due_at
                FROM tasks t LEFT JOIN domains d ON t.domain_id = d.id
                WHERE t.status = 'open'
                ORDER BY t.priority DESC, t.due_at ASC
            """).fetchall()
            header = "📋 כל המשימות הפתוחות"

        conn.close()

        if not rows:
            finish(args, f"{RLM}{header}\n{RLM}אין משימות.")
            return

        lines = [f"{RLM}{header}", ""]
        for r in rows:
            due = ""
            if r["due_at"]:
                try:
                    dt = datetime.fromisoformat(r["due_at"]).replace(tzinfo=timezone.utc).astimezone(JLM)
                    due = f" — {dt.strftime('%d/%m %H:%M')}"
                except Exception:
                    pass
            domain_str = f" [{r['domain']}]" if r["domain"] else ""
            lines.append(f"{RLM}• [P{r['priority']}] {r['title']}{domain_str}{due}")

        finish(args, "\n".join(lines))
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")

if __name__ == "__main__":
    main()
