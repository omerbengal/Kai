#!/usr/bin/env python3
"""
Typing loop launcher — starts typing_loop.py as a truly detached Windows process.

Usage:
    python D:/Kai/scripts/start_typing.py <chat_id> <worker_id>

worker_id is REQUIRED — shared typing_pid.txt is deprecated (orphans leak).
Saves PID to D:/Kai/tmp/typing_pid_{worker_id}.txt.

The typing loop auto-exits when its PID file is deleted or overwritten by another
process — no arbitrary timeout needed.
"""
import subprocess
import sys
import os

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000

TYPING_LOOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "typing_loop.py")
TMP_DIR = "D:/Kai/tmp"


def main():
    if len(sys.argv) < 3:
        print("Usage: start_typing.py <chat_id> <worker_id>", file=sys.stderr)
        print("worker_id is REQUIRED — shared typing_pid.txt is deprecated (orphans leak).", file=sys.stderr)
        sys.exit(1)

    chat_id = sys.argv[1]
    worker_id = sys.argv[2]
    initial_delay = sys.argv[3] if len(sys.argv) >= 4 else "0"

    pid_file = os.path.join(TMP_DIR, f"typing_pid_{worker_id}.txt")
    os.makedirs(TMP_DIR, exist_ok=True)

    proc = subprocess.Popen(
        [sys.executable, TYPING_LOOP, chat_id, pid_file, str(initial_delay)],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    with open(pid_file, "w") as f:
        f.write(str(proc.pid))

    print(proc.pid)


if __name__ == "__main__":
    main()
