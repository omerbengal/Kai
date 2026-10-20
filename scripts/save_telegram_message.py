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
import os
import io
from datetime import datetime, timezone


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

    for message_id, row_text in outgoing_rows(text, tool_input.get("files") or [], json.dumps(tool_response)):
        save_message(message_id, "outgoing", row_text)


def parse_sent_ids(response_str):
    """Message ids from the reply tool's result.

    One message: "sent (id: 171)". Several (long text split into chunks, or text + files):
    "sent 2 parts (ids: 171, 172)". Before 20/10/2026 only the first form was parsed, so every
    reply with a screenshot was saved with telegram_message_id NULL and a 👍 on it was "not found".
    """
    m = re.search(r"sent \(id: (\d+)\)", response_str)
    if m:
        return [int(m.group(1))]
    m = re.search(r"sent \d+ parts \(ids: ([\d,\s]+)\)", response_str)
    if m:
        return [int(x) for x in re.findall(r"\d+", m.group(1))]
    return []


def outgoing_rows(text, files, response_str):
    """(telegram_message_id, text) rows for one reply call.

    The plugin sends the text chunks first, then each file as its own message. The first id
    gets the full text; further text chunks and each file get a short marker row, so every
    sent message (a reaction target) has a row without duplicating the text in the history.
    """
    ids = parse_sent_ids(response_str)
    if not ids:
        return [(None, text)]
    files = [os.path.basename(str(f)) for f in files]
    n_files = min(len(files), len(ids))
    n_text = len(ids) - n_files
    rows = []
    for i, mid in enumerate(ids):
        if i < n_text:
            row_text = text if i == 0 else f"[המשך ההודעה {ids[0]}, חלק {i + 1}]"
        else:
            name = files[i - n_text]
            if i == 0:  # files only - the first file carries the text
                row_text = f"[קובץ: {name}] {text}".strip()
            else:
                row_text = f"[קובץ: {name}] (צורף להודעה {ids[0]})"
        rows.append((mid, row_text))
    return rows


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
    # Wrapped only when run as a hook (importing it in tests must not replace the streams)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
    main()
