#!/usr/bin/env python3
"""
Fast task creation.
Usage: python fast_task_create.py --title "כותרת" --priority 5 --domain "work" [--due_at "2026-04-13 09:00:00"] [--notes "הערות"] --ack_id N --orig_id N
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
import local_tz  # home timezone (travel-mode aware) - scripts/local_tz.py

def main():
    p = base_args("Create task")
    p.add_argument("--title", required=True)
    p.add_argument("--priority", type=int, default=5, help="0-11, default 5")
    p.add_argument("--domain", default=None, help="Domain name or Hebrew alias; omit to leave unset")
    p.add_argument("--due_at", default=None, help="UTC 'YYYY-MM-DD HH:MM:SS' (legacy; prefer --due_local)")
    p.add_argument("--due_local", default=None, help="LOCAL due time 'YYYY-MM-DD HH:MM' (home tz / travel destination)")
    p.add_argument("--israel_time", action="store_true", help="Interpret --due_local as Israel time even while abroad")
    p.add_argument("--notes", default=None)
    args = p.parse_args()

    start_typing(args.chat_id, args.orig_id)
    try:
        due_at = args.due_at
        if args.due_local:
            due_at = local_tz.local_str_to_utc_str(args.due_local, force_israel=args.israel_time)

        conn = get_db()

        # Resolve domain — fuzzy match, case-insensitive
        domain_id = None
        domain_display = None
        if args.domain:
            domain_row = conn.execute(
                "SELECT id, name FROM domains WHERE lower(name) = lower(?) OR lower(name) LIKE lower(?)",
                (args.domain, f"%{args.domain}%")
            ).fetchone()
            if domain_row:
                domain_id = domain_row["id"]
                domain_display = domain_row["name"]
            # Silently proceed without domain if not found (don't fail for missing domain)

        conn.execute(
            "INSERT INTO tasks (title, notes, priority, domain_id, due_at, status) VALUES (?, ?, ?, ?, ?, 'open')",
            (args.title, args.notes, args.priority, domain_id, due_at)
        )
        conn.commit()
        conn.close()

        domain_str = f" | 🏷 {domain_display}" if domain_display else ""
        action_log("fast_task_create", "created", f'"{args.title}" P{args.priority}{domain_str}')
        due_str = f'\n{RLM}📅 {fmt_when(due_at, "%d/%m %H:%M", force_israel=args.israel_time)}' if due_at else ''
        finish(args, f'{RLM}✅ משימה נוספה!\n{RLM}📌 {args.title}\n{RLM}⚡ עדיפות: {args.priority}{domain_str}{due_str}')
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")

if __name__ == "__main__":
    main()
