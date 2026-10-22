#!/usr/bin/env python3
"""
WhatsApp Bridge Watchdog — standalone Python script for Windows Task Scheduler.
Runs every 5 minutes. Checks if the WhatsApp bridge is alive on port 8080.
If not running, restarts start_whatsapp_bridge.bat and logs to DB.
"""

import socket
import subprocess
import sqlite3
from datetime import datetime, timezone

import night_mode  # same directory; defines the nightly downtime window

DB_PATH = r"D:\Kai\kai.db"
LOG_FILE = r"D:\Kai\tmp\whatsapp-bridge.log"
BAT_FILE = r"D:\Kai\start_whatsapp_bridge.bat"
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 8080
TIMEOUT_SECONDS = 3


def is_bridge_running() -> bool:
    """Try to open a TCP connection to the bridge port. Returns True if bridge is up."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(TIMEOUT_SECONDS)
        s.connect((BRIDGE_HOST, BRIDGE_PORT))
        s.close()
        return True
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False


def log_to_db(action: str, details: str):
    """Log an event to the action_log table."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.text_factory = str
        conn.execute(
            "INSERT INTO action_log (agent, action, details, created_at) VALUES (?, ?, ?, datetime('now'))",
            ("whatsapp_watchdog", action, details)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        # Can't log to DB — write to log file as fallback
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                ts = datetime.now(timezone.utc).isoformat()
                f.write(f"[{ts}] watchdog DB log failed: {e}\n")
        except Exception:
            pass


def restart_bridge():
    """Start the bridge bat file in a detached background process."""
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", BAT_FILE],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
            close_fds=True,
        )
        log_to_db("restart", "Bridge was down on port 8080 — restarted via start_whatsapp_bridge.bat")
        # Also append to bridge log file
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                ts = datetime.now(timezone.utc).isoformat()
                f.write(f"[{ts}] watchdog: bridge was down, restart triggered\n")
        except Exception:
            pass
    except Exception as e:
        log_to_db("restart_failed", f"Failed to restart bridge: {e}")


def main():
    # The bridge is deliberately down overnight. Restarting it here would undo the
    # nightly shutdown within five minutes, so stand down for the duration.
    if night_mode.should_stand_down("whatsapp_bridge_watchdog"):
        return

    if is_bridge_running():
        log_to_db("check", "Bridge is up on port 8080 — OK")
    else:
        restart_bridge()


if __name__ == "__main__":
    main()
