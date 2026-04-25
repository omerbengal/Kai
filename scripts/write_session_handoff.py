#!/usr/bin/env python3
"""
write_session_handoff.py
Called by restart_claude.ps1 before launching the new session.
Reads the last N messages from kai.db and writes a handoff summary
to D:/Kai/tmp/session_handoff.md so the new session knows the context.
"""

import sqlite3
import sys
import os
from datetime import datetime, timezone, timedelta

HANDOFF_FILE = "D:/Kai/tmp/session_handoff.md"
DB_PATH = "D:/Kai/kai.db"
JERUSALEM = timezone(timedelta(hours=3))
NUM_MESSAGES = 10


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = str
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        "SELECT direction, text, ts FROM messages ORDER BY ts DESC LIMIT ?",
        (NUM_MESSAGES,)
    )
    rows = list(reversed(cur.fetchall()))
    conn.close()

    if not rows:
        return

    lines = [f"הודעות אחרונות לפני ריסטארט ({datetime.now(JERUSALEM).strftime('%Y-%m-%d %H:%M')} ירושלים):\n"]
    for r in rows:
        ts_raw = r["ts"]
        try:
            dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).astimezone(JERUSALEM)
            ts_str = dt.strftime("%H:%M")
        except Exception:
            ts_str = ts_raw[:16]

        direction = "עומר" if r["direction"] == "incoming" else "קאי"
        text = (r["text"] or "").strip()[:200]
        lines.append(f"[{ts_str}] {direction}: {text}")

    content = "\n".join(lines)

    os.makedirs(os.path.dirname(HANDOFF_FILE), exist_ok=True)
    with open(HANDOFF_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
    print(f"Handoff written: {len(rows)} messages -> {HANDOFF_FILE}")


if __name__ == "__main__":
    main()
