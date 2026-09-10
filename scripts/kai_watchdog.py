#!/usr/bin/env python3
"""
Kai Watchdog — standalone Python script for Windows Task Scheduler.
Runs every 2 minutes. Checks if claude.exe is running.
If not running, sends Telegram alert, restarts via restart_claude.ps1, and logs to DB.
"""

import os
import subprocess
import sqlite3
import urllib.request
import urllib.parse
from datetime import datetime, timezone

import night_mode  # same directory; defines the nightly downtime window

DB_PATH = r"D:\Kai\kai.db"
BOT_TOKEN = os.environ["KAI_TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
RESTART_SCRIPT = r"D:\Kai\restart_claude.ps1"
RESTART_WRAPPER = r"D:\Kai\scripts\run_restart_logged.ps1"
RESTART_TASK = "KaiRestart"          # separate Task Scheduler task = own job object
LOCK_FILE = r"D:\Kai\tmp\restart_in_progress"
DOWN_FILE = r"D:\Kai\tmp\claude_down_cycles"
LOCK_TTL = 240                       # seconds a restart is considered "in progress"
ALERT_AFTER_CYCLES = 3               # ~6 min of downtime before shouting "still down"
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def is_claude_running() -> bool:
    """Check if claude.exe is in the process list.

    Uses PowerShell Get-Process (NT API) instead of `tasklist /FI` because the
    latter goes through WMI and hangs >30s on this host, causing the watchdog
    to falsely conclude claude is dead and trigger restart cascades.
    """
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "if (Get-Process -Name claude -ErrorAction SilentlyContinue) {'YES'} else {'NO'}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "YES" in result.stdout
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


def restart_kai() -> str:
    """Trigger the restart OUTSIDE this watchdog's Task Scheduler job object.

    Root cause this solves: KaiWatchdog runs with UseUnifiedSchedulingEngine=true,
    so Task Scheduler runs it inside a job object and tears down the whole process
    tree when the task ends. A plain Popen child (even DETACHED_PROCESS, which does
    NOT break out of a job) was therefore killed a few seconds in, before
    restart_claude.ps1 ever reached its `Start-Process wt.exe` line. No terminal ever
    opened, yet the watchdog reported "restart failed" as if the script had run.

    Primary path: `schtasks /run /tn KaiRestart` — the KaiRestart task gets its own
    job object and survives this process exiting.
    Fallback: Popen with CREATE_BREAKAWAY_FROM_JOB (works only if the job allows
    breakaway, hence it is the fallback, not the default).

    Returns the path taken, for logging.
    """
    try:
        result = subprocess.run(
            ["schtasks", "/run", "/tn", RESTART_TASK],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if result.returncode == 0:
            return "schtasks"
        err = (result.stdout or "") + (result.stderr or "")
    except Exception as e:
        err = str(e)

    # Fallback — break out of the job object explicitly.
    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-WindowStyle", "Hidden", "-File", RESTART_WRAPPER],
        creationflags=(subprocess.DETACHED_PROCESS
                       | subprocess.CREATE_NO_WINDOW
                       | CREATE_BREAKAWAY_FROM_JOB),
        close_fds=True,
    )
    return f"breakaway_fallback (schtasks failed: {err.strip()[:120]})"


def _read_int(path: str) -> int:
    try:
        with open(path) as f:
            return int(f.read().strip() or 0)
    except Exception:
        return 0


def _write_int(path: str, value: int):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(str(value))
    except Exception:
        pass


def _remove(path: str):
    try:
        os.remove(path)
    except Exception:
        pass


def restart_in_progress() -> bool:
    """True if a restart was triggered recently and hasn't finished yet.

    Prevents the cascade seen on 8-Aug (27 attempts in 52 min), where each new
    2-minute cycle launched another restart whose Step 5 killed the claude.exe the
    previous one had just started. The wrapper deletes the lock when it finishes;
    LOCK_TTL is the safety net if the wrapper itself dies.
    """
    try:
        age = datetime.now().timestamp() - os.path.getmtime(LOCK_FILE)
    except Exception:
        return False
    if age > LOCK_TTL:
        _remove(LOCK_FILE)
        return False
    return True


def check_reaction_channel():
    """Investigation #025 — alert if reactions are stale > 24h while incoming
    messages are still flowing. That pattern indicates the Telegram
    `message_reaction` update channel is broken (typically: stale plugin
    process serving an old `allowed_updates` list)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.text_factory = str
        last_reaction = conn.execute(
            "SELECT MAX(ts) FROM message_reactions WHERE direction='incoming'"
        ).fetchone()[0]
        last_incoming = conn.execute(
            "SELECT MAX(ts) FROM messages WHERE direction='incoming'"
        ).fetchone()[0]
        already_alerted = conn.execute(
            "SELECT 1 FROM action_log "
            "WHERE agent='kai_watchdog' AND action='reaction_channel_stale' "
            "AND created_at > datetime('now', '-12 hours') LIMIT 1"
        ).fetchone()
        conn.close()
    except Exception:
        return

    def hrs_ago(ts):
        if not ts:
            return 99999.0
        try:
            s = ts.replace("Z", "+00:00") if "T" in ts and ts.endswith("Z") else ts
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        except Exception:
            return 99999.0

    reaction_age = hrs_ago(last_reaction)
    incoming_age = hrs_ago(last_incoming)

    # Smart trigger (investigation #026): alert only if 3+ reminder-style
    # outgoing messages were sent in the last 30 min without any incoming
    # reaction. This catches stuck reminder loops fast (≤30 min) while
    # never false-alarming on quiet reaction days.
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.text_factory = str
        stuck_reminder_count = conn.execute(
            "SELECT COUNT(*) FROM messages "
            "WHERE direction='outgoing' "
            "  AND text LIKE '%🔔%' "
            "  AND ts > datetime('now', '-30 minutes')"
        ).fetchone()[0]
        conn.close()
    except Exception:
        return

    if stuck_reminder_count >= 3 and reaction_age > 1 and not already_alerted:
        text = (
            "⚠️ ערוץ הריאקציות כנראה שבור!\n"
            f"3+ תזכורות נשלחו ב-30 דקות האחרונות, אף ריאקציה לא נקלטה.\n"
            f"ריאקציה אחרונה: {last_reaction}\n"
            "תזכורות לא ייסגרו אוטומטית עד restart של הסשן."
        )
        send_telegram(text)
        log_to_db(
            "reaction_channel_stale",
            f"stuck_reminders_30m={stuck_reminder_count} reaction_age_hrs={reaction_age:.1f}",
        )


def check_telegram_patch():
    """Investigation #033 — keep the Telegram plugin patch healthy.

    Every run (every 2 min) we:
      1. Re-apply the patch to all server.ts files (idempotent, NO process kill —
         we never pass --session-start, so the live plugin process is untouched).
         This re-patches + read-only-locks any file a plugin update reverted, and
         catches a freshly-cached NEW plugin version before Claude spawns from it.
      2. Detect whether the *running* plugin process is serving unpatched (broken)
         code. If so, alert Omer once per 12h to /reload-plugins — mid-session we
         cannot safely respawn bun ourselves (killing it leaves the channel dead).
    """
    # 1. Idempotent re-patch (no kill) — self-heals reverted / new-version files.
    try:
        subprocess.run(
            ["python", "D:/Kai/scripts/patch_telegram_plugin.py"],
            capture_output=True, timeout=60, check=False,
        )
    except Exception:
        pass  # watchdog must never crash

    # 2. Is the RUNNING plugin process serving patched code?
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='bun.exe' OR Name='node.exe'\" | "
             "Where-Object { $_.CommandLine -like '*claude-plugins-official*telegram*' } | "
             "ForEach-Object { $_.CommandLine } | Select-Object -First 1"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        cmdline = (result.stdout or "").strip()
    except Exception:
        return
    if not cmdline:
        return  # no running plugin process — the claude-down path handles that

    # Extract --cwd <dir> and check the server.ts it runs.
    import re, os
    m = re.search(r"--cwd[= ]+\"?([^\"\s]+)", cmdline)
    if not m:
        return
    server_path = os.path.join(m.group(1), "server.ts")
    try:
        content = open(server_path, encoding="utf-8").read()
    except Exception:
        return
    patched = (
        "reply_to_message_id" in content
        and "event_type: 'reaction'" in content
        and "inline_keyboard" in content
        and "Non-perm callback" in content
    )
    if patched:
        return  # running process is healthy

    # Running process serves broken code — alert once per 12h.
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.text_factory = str
        already = conn.execute(
            "SELECT 1 FROM action_log WHERE agent='kai_watchdog' "
            "AND action='telegram_patch_broken' "
            "AND created_at > datetime('now','-12 hours') LIMIT 1"
        ).fetchone()
        conn.close()
    except Exception:
        already = None
    if not already:
        send_telegram(
            "⚠️ הפלאגין של טלגרם רץ בקוד לא-מתוקן (ריאקציות/כפתורים עלולים לא לעבוד).\n"
            "תיקנתי את הקובץ בדיסק - הרץ /reload-plugins בטרמינל כדי לרענן את התהליך."
        )
        log_to_db("telegram_patch_broken", f"running server unpatched: {server_path}")


def main():
    # Claude is deliberately down overnight. Every check below would read that as a
    # crash: it would restart the session within two minutes and push three alerts
    # to a sleeping Omer. Stand down instead, and clear the down-cycle counter so
    # the morning's first real check starts from a clean slate rather than
    # announcing a "recovery" that was really just the scheduled startup.
    if night_mode.should_stand_down("kai_watchdog"):
        _remove(DOWN_FILE)
        _remove(LOCK_FILE)
        return

    check_reaction_channel()
    check_telegram_patch()

    down_cycles = _read_int(DOWN_FILE)

    if is_claude_running():
        # Claude is up. If we were mid-recovery, report the recovery once, then reset.
        if down_cycles:
            log_to_db("restart_success", f"claude.exe back up after {down_cycles} down cycle(s)")
            send_telegram("✅ קאי חזר לפעול")
        _remove(DOWN_FILE)
        _remove(LOCK_FILE)
        return

    # --- Claude is NOT running ---
    down_cycles += 1
    _write_int(DOWN_FILE, down_cycles)

    # Guard: a restart is already underway — do not stomp it with another one.
    if restart_in_progress():
        log_to_db("restart_waiting", f"restart already in progress; down_cycles={down_cycles}")
    else:
        ts = datetime.now(timezone.utc).isoformat()
        _write_int(LOCK_FILE, int(datetime.now().timestamp()))
        if down_cycles == 1:
            send_telegram("⚠️ קאי לא רץ — מבצע ריסטרט אוטומטי...")
        try:
            path = restart_kai()
            log_to_db("restart_attempt",
                      f"claude.exe not found at {ts} — triggered via {path}; down_cycles={down_cycles}")
        except Exception as e:
            _remove(LOCK_FILE)
            log_to_db("restart_failed", f"Failed to trigger restart: {e}")
            send_telegram(f"❌ לא הצלחתי בכלל להפעיל את הריסטרט — {e}")
            return

    # NO verdict in this run. Booting a session takes far longer than one cycle,
    # and the old 15-second single check is what produced the false "restart failed"
    # alarms. The next cycle (2 min) observes reality instead.
    #
    # Genuine-failure path: still down after ALERT_AFTER_CYCLES cycles (~6 min),
    # alerted at most once per 30 minutes so it never becomes a spam loop.
    if down_cycles >= ALERT_AFTER_CYCLES:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.text_factory = str
            recent = conn.execute(
                "SELECT 1 FROM action_log WHERE agent='kai_watchdog' "
                "AND action='still_down_alert' "
                "AND created_at > datetime('now','-30 minutes') LIMIT 1"
            ).fetchone()
            conn.close()
        except Exception:
            recent = None
        if not recent:
            send_telegram(
                f"❌ קאי עדיין לא רץ אחרי {down_cycles} ניסיונות ריסטרט (כ-{down_cycles * 2} דקות).\n"
                "בדוק את D:\\Kai\\tmp\\restart.log כדי לראות איפה הריסטרט נתקע."
            )
            log_to_db("still_down_alert", f"down_cycles={down_cycles}")


if __name__ == "__main__":
    main()
