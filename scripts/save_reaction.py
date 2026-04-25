#!/usr/bin/env python3
"""
Save Kai's outgoing Telegram reactions to message_reactions table.
Called via PostToolUse hook on mcp__plugin_telegram_telegram__react.
Receives JSON on stdin with tool_input containing chat_id, message_id, emoji.
"""
import sys
import json
import sqlite3
import io
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

DB_PATH = "D:/Kai/kai.db"


def main():
    try:
        raw = sys.stdin.buffer.read()
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return

    tool_input = data.get("tool_input", {})
    message_id = tool_input.get("message_id")
    emoji = tool_input.get("emoji")

    if not message_id or not emoji:
        return

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO message_reactions (telegram_message_id, direction, emoji, action, ts) "
            "VALUES (?, 'outgoing', ?, 'add', ?)",
            (int(message_id), emoji, ts)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[save_reaction] error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
