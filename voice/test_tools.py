"""
Tests for voice/tools.py against the real kai.db.
Every row created here is marked [VOICE-TEST] and hard-deleted at the end (reminders, tasks and
the action_log rows they produced), so the DB is left as it was.

Run: D:/Kai/.venv/Scripts/python.exe D:/Kai/voice/test_tools.py
"""
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tools  # noqa: E402

DB = "D:/Kai/kai.db"
MARK = "[VOICE-TEST]"
failures = []


def check(cond, label):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        failures.append(label)


def db_row(sql, params):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    r = c.execute(sql, params).fetchone()
    c.close()
    return r


def timed(name, **args):
    t = time.perf_counter()
    r = tools.call_tool(name, args)
    print(f"     {name} {args} -> {(time.perf_counter() - t) * 1000:.1f}ms")
    return r


def main():
    rid = tid = None
    try:
        r = timed("list_reminders")
        check(r["ok"] and isinstance(r["reminders"], list), "list_reminders returns a list")
        r = timed("list_tasks", filter="all")
        check(r["ok"] and r["count"] == len(r["tasks"]), "list_tasks all")
        check(timed("list_tasks", filter="today")["ok"], "list_tasks today")
        check(timed("list_tasks", filter="overdue")["ok"], "list_tasks overdue")
        r = timed("get_calendar", range="today")
        check(r["ok"], f"get_calendar today ({r.get('count')} events)")
        r = timed("get_calendar", range="upcoming", days=3)
        check(r["ok"], f"get_calendar upcoming ({r.get('count')} events)")

        # --- reminder: create -> verify UTC in DB -> reschedule -> cancel
        r = timed("create_reminder", message=f"{MARK} בדיקה", when_local="2030-01-15 20:30")
        rid = r.get("id")
        check(r["ok"] and rid, "create_reminder")
        row = db_row("SELECT * FROM reminders WHERE id=?", (rid,))
        # Jan in Israel = UTC+2 -> 18:30 UTC. Proves local_tz conversion, not a hard-coded +3.
        check(row and row["trigger_at"] == "2030-01-15 18:30:00" and row["status"] == "pending",
              f"reminder stored as UTC with space separator ({row and row['trigger_at']})")
        check(r["when_local"].startswith("2030-01-15 20:30"), "reminder echoed back in local time")

        r = timed("reschedule_reminder", keyword=MARK, new_when_local="2030-07-01 09:00")
        row = db_row("SELECT * FROM reminders WHERE id=?", (rid,))
        check(r["ok"] and row["trigger_at"] == "2030-07-01 06:00:00", "reschedule_reminder (summer UTC+3)")
        check(timed("reschedule_reminder", keyword="zzz-no-such")["ok"] is False, "reschedule with nothing to change fails")
        check(timed("create_reminder", message="x", when_local="tomorrow")["ok"] is False, "bad time format rejected")

        r = timed("cancel_reminder", reminder_id=rid)
        row = db_row("SELECT status FROM reminders WHERE id=?", (rid,))
        check(r["ok"] and row["status"] == "dismissed", "cancel_reminder -> dismissed")

        # --- task: create -> verify -> ambiguity -> complete
        r = timed("create_task", title=f"{MARK} משימת בדיקה", priority=3, domain="עבודה", due_local="2030-01-15 10:00")
        tid = r.get("id")
        row = db_row("SELECT t.*, d.name AS dn FROM tasks t LEFT JOIN domains d ON d.id=t.domain_id WHERE t.id=?", (tid,))
        check(r["ok"] and row["status"] == "open" and row["priority"] == 3 and row["dn"] == "work"
              and row["due_at"] == "2030-01-15 08:00:00", "create_task stored correctly (domain alias, UTC due)")
        r2 = timed("create_task", title=f"{MARK} משימת בדיקה 2")
        amb = timed("complete_task", keyword=MARK)
        check(amb["ok"] is False and amb.get("error") == "ambiguous" and len(amb["candidates"]) == 2,
              "complete_task refuses to guess between 2 matches")
        r = timed("complete_task", task_id=tid)
        row = db_row("SELECT status, completed_at FROM tasks WHERE id=?", (tid,))
        check(r["ok"] and row["status"] == "done" and row["completed_at"], "complete_task -> done")
        tools.call_tool("complete_task", {"task_id": r2["id"]})
    finally:
        c = sqlite3.connect(DB)
        c.execute("DELETE FROM reminders WHERE message LIKE ?", (f"%{MARK}%",))
        c.execute("DELETE FROM tasks WHERE title LIKE ?", (f"%{MARK}%",))
        c.execute("DELETE FROM action_log WHERE agent='voice' AND details LIKE ?", (f"%{MARK}%",))
        c.commit()
        left = c.execute("SELECT (SELECT count(*) FROM reminders WHERE message LIKE ?) + "
                         "(SELECT count(*) FROM tasks WHERE title LIKE ?)", (f"%{MARK}%", f"%{MARK}%")).fetchone()[0]
        c.close()
        check(left == 0, "cleanup: no test rows left in DB")

    print(f"\n{'ALL PASSED' if not failures else str(len(failures)) + ' FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
