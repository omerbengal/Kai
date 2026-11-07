#!/usr/bin/env python
"""
Record a spawned worker in tmp/task_acks.json so Task Notification Handling
(agents/listener.md) can unpin + delete its ACK when the worker finishes.

Replaces the inline python block the dispatcher used to write after every spawn.

Usage:
    python D:/Kai/scripts/dispatch_map.py <agent_id> <ack_msg_id> <orig_msg_id> [chat_id]

chat_id (07/11/2026): pass -1004297864522 for a General-topic worker, so the finish handler unpins and
deletes the ACK in the GROUP and never touches the private message / messages row with the same id.
"""
import json
import os
import sys
import tempfile

MAPPING = "D:/Kai/tmp/task_acks.json"


def main():
    if len(sys.argv) not in (4, 5):
        print("usage: dispatch_map.py <agent_id> <ack_msg_id> <orig_msg_id> [chat_id]")
        sys.exit(2)
    agent_id, ack_id, orig_id = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    chat_id = sys.argv[4] if len(sys.argv) == 5 else "${KAI_TELEGRAM_CHAT_ID}"
    try:
        with open(MAPPING, "r", encoding="utf-8") as f:
            mapping = json.load(f)
        if not isinstance(mapping, dict):
            mapping = {}
    except Exception:
        mapping = {}
    mapping[agent_id] = {"ack_id": ack_id, "orig_id": orig_id}
    if chat_id != "${KAI_TELEGRAM_CHAT_ID}":
        mapping[agent_id]["chat_id"] = chat_id
    os.makedirs(os.path.dirname(MAPPING), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(MAPPING), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(mapping, f)
    os.replace(tmp, MAPPING)  # atomic: parallel dispatcher turns may write too
    print("ok")


if __name__ == "__main__":
    main()
