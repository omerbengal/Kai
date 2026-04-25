#!/usr/bin/env python3
"""
Kai Watchdog — standalone Python script for Windows Task Scheduler.
Runs every 2 minutes. Checks if claude.exe is running.
If not running, sends Telegram alert, restarts via restart_claude.ps1, and logs to DB.
"""

import subprocess
import sqlite3
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

DB_PATH = r"D:\Kai\kai.db"
BOT_TOKEN = __import__("os").environ["KAI_TELEGRAM_BOT_TOKEN"]
CHAT_ID = __import__("os").environ.get("KAI_TELEGRAM_CHAT_ID", "")
RESTART_SCRIPT = r"D:\Kai\restart_claude.ps1"
WAIT_AFTER_RESTART = 60  # seconds to wait before checking if claude is back up


def is_claude_running() -> bool:
    """Check if claude.exe is in the process list."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq claude.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "claude.exe" in result.stdout
    except Exception:
        return False


def send_telegram(text: str):
    """Send a Telegram message to Omer."""
    try:
        encoded = urllib.parse.quote(text)
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id={CHAT_ID}&text={encoded}"
        urllib.request.urlopen(url, timeout=10)
    except Exception:
        pass  # Silent — watchdog must never crash on Telegram failure


def log_to_db(action: str, details: str):
    """Log an event to the action_log table."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.text_factory = str
        conn.execute(
            "INSERT INTO action_log (agent, action, details, created_at) VALUES (?, ?, ?, datetime('now'))",
            ("kai_watchdog", action, details),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Can't log — watchdog must never crash


def restart_kai():
    """Launch restart_claude.ps1 in a detached background process."""
    subprocess.Popen(
        [
            "powershell",
            "-WindowStyle",
            "Hidden",
            "-NonInteractive",
            "-File",
            RESTART_SCRIPT,
        ],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        close_fds=True,
    )


def main():
    if is_claude_running():
        # Claude is up — silent exit, no noise
        return

    # Claude is NOT running
    ts = datetime.now(timezone.utc).isoformat()
    log_to_db("restart_attempt", f"claude.exe not found at {ts} — triggering restart")

    send_telegram("⚠️ קאי לא רץ — מבצע ריסטרט אוטומטי...")

    try:
        restart_kai()
    except Exception as e:
        log_to_db("restart_failed", f"Failed to launch restart_claude.ps1: {e}")
        send_telegram(f"❌ ריסטרט נכשל — שגיאה בהפעלת הסקריפט: {e}")
        return

    # Wait for Claude to come up
    time.sleep(WAIT_AFTER_RESTART)

    if is_claude_running():
        log_to_db("restart_success", "claude.exe detected after restart")
        send_telegram("✅ קאי הופעל מחדש בהצלחה")
    else:
        log_to_db("restart_unconfirmed", "claude.exe still not detected after restart wait")
        send_telegram("❌ ריסטרט נכשל — claude.exe עדיין לא רץ. בדוק ידנית")


if __name__ == "__main__":
    main()
