#!/usr/bin/env python3
"""
Fast action log — show recent agent activity.
Usage: python fast_action_log.py --hours 24 --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

Use --hours to control the time window (default: 24h).
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
from datetime import datetime, timezone, timedelta

MAX_ENTRIES = 40


def main():
    p = base_args("Show recent action log")
    p.add_argument("--hours", type=int, default=24, help="How many hours back to look (default: 24)")
    args = p.parse_args()

    start_typing(args.chat_id, args.orig_id)
    try:
        conn = get_db()
        since_utc = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).strftime("%Y-%m-%d %H:%M:%S")

        rows = conn.execute("""
            SELECT agent, action, details, created_at
            FROM action_log
            WHERE created_at >= ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (since_utc, MAX_ENTRIES)).fetchall()
        conn.close()

        if not rows:
            finish(args, f"{RLM}📋 לא נמצאו פעולות ב-{args.hours} השעות האחרונות.")
            return

        lines = [f"{RLM}📋 {len(rows)} פעולות ב-{args.hours}ש האחרונות:"]
        lines.append(f"{RLM}{'─' * 28}")

        for r in rows:
            ts_raw = r["created_at"]
            try:
                if isinstance(ts_raw, str):
                    dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
                dt_jlm = dt + timedelta(hours=3)
                time_str = dt_jlm.strftime("%H:%M")
            except Exception:
                time_str = str(ts_raw)[:16]

            agent = (r["agent"] or "?").replace("fast_", "")
            action = r["action"] or ""
            details = (r["details"] or "")[:80]
            lines.append(f"{RLM}[{time_str}] {agent} → {action} | {details}")

        action_log("fast_action_log", "list", f"hours={args.hours} count={len(rows)}")
        finish(args, "\n".join(lines))
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")


if __name__ == "__main__":
    main()
