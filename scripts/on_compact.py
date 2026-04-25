#!/usr/bin/env python3
"""
on_compact.py — Hook fired by Claude Code on PreCompact or PostCompact events.

Called via settings.json hooks with stdin JSON:
  {
    "hook_event_name": "PreCompact" | "PostCompact",
    "compaction_trigger": "auto" | "manual",
    "session_id": "...",
    "transcript_path": "..."
  }

Sends a Telegram notification and logs to DB.
"""

import sys
import json
import sqlite3
import urllib.request
from datetime import datetime, timezone, timedelta

BOT_TOKEN = __import__("os").environ["KAI_TELEGRAM_BOT_TOKEN"]
CHAT_ID = __import__("os").environ.get("KAI_TELEGRAM_CHAT_ID", "")
DB_PATH = "D:/Kai/kai.db"
JERUSALEM = timezone(timedelta(hours=3))


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(url, payload, {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[on_compact] Telegram send failed: {e}", file=sys.stderr)


def log_to_db(event: str, trigger: str, session_id: str) -> None:
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.text_factory = str
        conn.execute(
            "INSERT INTO action_log (agent, action, details) VALUES (?, ?, ?)",
            ("on_compact", event, f"trigger={trigger} session={session_id}"),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[on_compact] DB log failed: {e}", file=sys.stderr)


def main() -> None:
    # Read hook input from stdin
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}

    event = data.get("hook_event_name", "PostCompact")
    trigger = data.get("compaction_trigger", "auto")
    session_id = data.get("session_id", "unknown")

    now_local = datetime.now(JERUSALEM).strftime("%H:%M")

    if trigger == "auto":
        trigger_label = "אוטומטי"
        emoji = "🗜️"
    else:
        trigger_label = "ידני"
        emoji = "📦"

    if event == "PreCompact":
        msg = f"\u200F{emoji} קומפקט מתחיל ({trigger_label}) — {now_local}\nהסשן הגיע לגבול הקונטקסט."
    else:  # PostCompact
        msg = f"\u200F{emoji} קומפקט הסתיים ({trigger_label}) — {now_local}\nהיסטוריית השיחה סוכמה. הקונטקסט אופס."

    send_telegram(msg)
    log_to_db(event, trigger, session_id)


if __name__ == "__main__":
    main()
