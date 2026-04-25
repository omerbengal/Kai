#!/usr/bin/env python3
"""
Stop a specific typing loop started by start_typing.py.

Usage:
    python D:/Kai/scripts/stop_typing.py [worker_id]

worker_id (optional): same worker_id used in start_typing.py.
  With worker_id: reads D:/Kai/tmp/typing_pid_{worker_id}.txt
  Without: reads D:/Kai/tmp/typing_pid.txt (backward compat)

Safe to call even if no typing loop is running.
"""
import os

TMP_DIR = "D:/Kai/tmp"


def main():
    import sys
    if len(sys.argv) < 2:
        # silent no-op for legacy callers — nothing to stop without worker_id
        return
    worker_id = sys.argv[1]
    pid_file = os.path.join(TMP_DIR, f"typing_pid_{worker_id}.txt")

    if not os.path.exists(pid_file):
        return

    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())
    except (ValueError, IOError):
        try:
            os.remove(pid_file)
        except Exception:
            pass
        return

    try:
        import psutil
        psutil.Process(pid).kill()
    except Exception:
        pass

    try:
        os.remove(pid_file)
    except Exception:
        pass


if __name__ == "__main__":
    main()
