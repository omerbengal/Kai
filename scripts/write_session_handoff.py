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

sys.path.insert(0, "D:/Kai/scripts")
import local_tz  # Israel, or the travel-mode destination

HANDOFF_FILE = "D:/Kai/tmp/session_handoff.md"
DB_PATH = "D:/Kai/kai.db"
NUM_MESSAGES = 50  # Omer 22/10/2026: 10 was too little context after a restart
# transient status messages take slots without adding context
TRANSIENT_PREFIXES = ("🔄", "🎤 מתמלל", "🔍 מתחקר")


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = str
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ORDER BY id: ts mixes 'YYYY-MM-DD HH:MM' and ISO 'T' formats, which sort wrong as text
    cur.execute("SELECT direction, text, ts FROM messages ORDER BY id DESC LIMIT ?", (NUM_MESSAGES * 3,))
    rows = [r for r in cur.fetchall()
            if not (r["direction"] == "outgoing" and (r["text"] or "").lstrip().startswith(TRANSIENT_PREFIXES))]
    rows = list(reversed(rows[:NUM_MESSAGES]))
    conn.close()

    if not rows:
        return

    lines = [f"הודעות אחרונות לפני ריסטארט ({local_tz.now_local():%Y-%m-%d %H:%M} {local_tz.tz_label() or 'ישראל'}):\n"]
    for r in rows:
        ts_raw = r["ts"]
        try:
            ts_str = local_tz.fmt_local(ts_raw, "%H:%M", label=False)
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
