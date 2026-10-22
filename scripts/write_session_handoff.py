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
NUM_MESSAGES = 200  # Omer 22/10/2026: 10, then 50, was too little context after a restart
# No per-message cut (Omer 22/10: long Kai replies must be readable in full).
# Safety net only: if the total passes this, the OLDEST messages are dropped whole.
MAX_TOTAL_CHARS = 120_000
# transient status messages take slots without adding context
TRANSIENT_PREFIXES = ("🔄", "🎤 מתמלל", "🔍 מתחקר")


def main(out_path=HANDOFF_FILE):
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

    header = f"הודעות אחרונות לפני ריסטארט ({local_tz.now_local():%Y-%m-%d %H:%M} {local_tz.tz_label() or 'ישראל'}):\n"
    body = []
    for r in rows:
        ts_raw = r["ts"]
        try:
            ts_str = local_tz.fmt_local(ts_raw, "%H:%M", label=False)
        except Exception:
            ts_str = ts_raw[:16]

        direction = "עומר" if r["direction"] == "incoming" else "קאי"
        text = (r["text"] or "").strip()  # full text, never cut (Omer 22/10/2026)
        body.append(f"[{ts_str}] {direction}: {text}")

    # safety cap: drop the OLDEST whole messages until under the limit, never cut one mid-text
    total = len(header) + sum(len(b) + 1 for b in body)
    while body and total > MAX_TOTAL_CHARS:
        total -= len(body.pop(0)) + 1
    rows = body
    content = "\n".join([header] + body)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
    print(f"Handoff written: {len(rows)} messages ({len(content)} chars) -> {out_path}")


if __name__ == "__main__":
    # --out PATH writes elsewhere (test run without touching the real handoff file)
    main(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else HANDOFF_FILE)
