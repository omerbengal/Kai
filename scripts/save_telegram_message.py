#!/usr/bin/env python
"""
Save Telegram messages to kai.db messages table.

Called via hooks:
  - PostToolUse on reply tool  -> --mode outgoing
  - UserPromptSubmit           -> --mode incoming (auto-detects channel tag)
"""

import sys
import json
import sqlite3
import re
import io
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

DB_PATH = "D:/Kai/kai.db"


def save_message(telegram_message_id, direction, text, reply_to_message_id=None, reply_to_message_text=None, ts=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO messages (telegram_message_id, direction, text, reply_to_message_id, reply_to_message_text, ts)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            telegram_message_id,
            direction,
            text,
            reply_to_message_id,
            reply_to_message_text,
            ts or datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def handle_outgoing(data):
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", {})

    text = tool_input.get("text", "")

    # Parse message id from response text - e.g. "sent (id: 171)"
    response_str = json.dumps(tool_response)
    match = re.search(r"sent \(id: (\d+)\)", response_str)
    message_id = int(match.group(1)) if match else None

    save_message(message_id, "outgoing", text)


def handle_incoming(data):
    prompt = data.get("prompt", "") or data.get("content", "")

    # Match the <channel ...>text</channel> block (multiline safe)
    channel_match = re.search(r"<channel([\s\S]*?)>([\s\S]*?)</channel>", prompt)
    if not channel_match:
        return  # Not a Telegram message, nothing to save

    attrs_str = channel_match.group(1)
    text = channel_match.group(2).strip()

    def get_attr(name):
        m = re.search(f'{name}="(.*?)"', attrs_str, re.DOTALL)
        return m.group(1) if m else None

    message_id = get_attr("message_id")
    ts = get_attr("ts")
    reply_to_id = get_attr("reply_to_message_id")
    reply_to_text = get_attr("reply_to_message_text")

    if not message_id:
        return

    save_message(
        int(message_id),
        "incoming",
        text,
        int(reply_to_id) if reply_to_id else None,
        reply_to_text,
        ts,
    )


def main():
    try:
        raw = sys.stdin.buffer.read()
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return  # Can't parse input, silently exit

    # Determine mode from CLI arg or data shape
    mode = None
    if "--mode" in sys.argv:
        idx = sys.argv.index("--mode")
        if idx + 1 < len(sys.argv):
            mode = sys.argv[idx + 1]

    if mode is None:
        if "tool_name" in data:
            mode = "outgoing"
        elif "prompt" in data or "content" in data:
            mode = "incoming"

    if mode == "outgoing":
        handle_outgoing(data)
    elif mode == "incoming":
        handle_incoming(data)


if __name__ == "__main__":
    main()
