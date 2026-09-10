#!/usr/bin/env python3
"""
Fast task list query.
Usage: python fast_tasks_list.py [--filter today|all|overdue|domain|priority] [--domain "work"] [--priority 10] --ack_id N --orig_id N
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
import local_tz  # home timezone (travel-mode aware) - scripts/local_tz.py
from datetime import datetime, timezone, timedelta

JLM = local_tz.home_tz()  # was fixed UTC+3; travel-mode aware

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
            # DB stores 'YYYY-MM-DD HH:MM:SS' (space) - compare in the same format
            start = local_tz.utc_db_str(now.replace(hour=0, minute=0, second=0, microsecond=0))
            end = local_tz.utc_db_str(now.replace(hour=23, minute=59, second=59, microsecond=0))
            rows = conn.execute("""
                SELECT t.title, t.priority, d.name as domain, t.due_at
                FROM tasks t LEFT JOIN domains d ON t.domain_id = d.id
                WHERE t.status = 'open' AND t.due_at BETWEEN ? AND ?
                ORDER BY t.priority DESC
            """, (start, end)).fetchall()
            header = "📋 משימות להיום" + (f" ({local_tz.tz_label()})" if local_tz.tz_label() else "")

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

        def fmt_due(r):
            if not r["due_at"]:
                return ""
            try:
                dt = datetime.fromisoformat(r["due_at"]).replace(tzinfo=timezone.utc).astimezone(JLM)
                return f" - {dt.strftime('%d/%m %H:%M')}"
            except Exception:
                return ""

        if args.filter == "all":
            DOMAIN_META = {
                "work":     ("💼", "עבודה"),
                "personal": ("🏠", "אישי"),
                "health":   ("🏥", "בריאות"),
                "finance":  ("💰", "כספים"),
            }
            groups = {}
            for r in rows:
                dom = r["domain"] or "ללא תחום"
                groups.setdefault(dom, []).append(r)

            def dom_sort_key(d):
                order = {"work": 0, "personal": 1}
                return (order.get(d, 2), d)

            lines = [f"{RLM}{header} ({len(rows)}):", ""]
            for dom in sorted(groups.keys(), key=dom_sort_key):
                emoji, label = DOMAIN_META.get(dom, ("📌", dom))
                lines.append(f"{RLM}{emoji} {label} ({len(groups[dom])})")
                lines.append("")
                for r in groups[dom]:
                    lines.append(f"{RLM}• [P{r['priority']}] {r['title']}{fmt_due(r)}")
                    lines.append("")
            finish(args, "\n".join(lines).rstrip())
        else:
            lines = [f"{RLM}{header}", ""]
            for r in rows:
                domain_str = f" [{r['domain']}]" if r["domain"] else ""
                lines.append(f"{RLM}• [P{r['priority']}] {r['title']}{domain_str}{fmt_due(r)}")
                lines.append("")
            finish(args, "\n".join(lines).rstrip())
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")

if __name__ == "__main__":
    main()
