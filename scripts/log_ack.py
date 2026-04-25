"""
Log ACK message events to the ack_log table in kai.db.
Used by listener.md (dispatcher) after each edit_message call on the ACK.

Usage:
    python D:/Kai/scripts/log_ack.py --ack_id 1234 --action edited --text "new text"
    python D:/Kai/scripts/log_ack.py --ack_id 1234 --action deleted
    python D:/Kai/scripts/log_ack.py --ack_id 1234 --action sent --text "initial ACK text"
"""
import argparse
import sqlite3
from datetime import datetime, timezone

DB_PATH = "D:/Kai/kai.db"


def main():
    p = argparse.ArgumentParser(description="Log ACK message event to ack_log table")
    p.add_argument("--ack_id", required=True, type=int, help="ACK message ID")
    p.add_argument("--action", required=True, choices=["sent", "edited", "deleted"],
                   help="Type of event")
    p.add_argument("--text", default=None, help="Message text (for sent/edited events)")
    args = p.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.text_factory = str
        conn.execute(
            "INSERT INTO ack_log (ack_msg_id, action, text, ts) VALUES (?, ?, ?, ?)",
            (args.ack_id, args.action, args.text, ts)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"log_ack error: {e}")
        raise


if __name__ == "__main__":
    main()
