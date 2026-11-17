#!/usr/bin/env python3
"""
Kai Watchdog — standalone Python script for Windows Task Scheduler.
Runs every 2 minutes. Checks if claude.exe is running.
If not running, sends Telegram alert, restarts via restart_claude.ps1, and logs to DB.
"""

import json
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
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
# Investigation 044 (17/11/2026): this script runs as pythonw (no console), so EVERY console child it
# starts without this flag gets its own visible window. On a memory-starved PC those windows stayed
# open and piled up every 2 minutes ("20 terminals"). Every subprocess call below passes it.
CREATE_NO_WINDOW = 0x08000000

ATTEMPTS_FILE = "D:/Kai/tmp/claude_restart_attempts"   # restarts triggered in this outage
GAVE_UP_FILE = "D:/Kai/tmp/claude_restart_gave_up"     # the one "gave up" alert was sent
RETRY_HANDLED_FILE = "D:/Kai/tmp/watchdog_retry_handled.json"
RESTART_LOG = "D:/Kai/tmp/restart.log"
MAX_RESTART_ATTEMPTS = 3            # then stop and ask Omer (button), never loop forever
RETRY_CALLBACK = "watchdog_retry_restart"
LOW_MEMORY_GB = 3.0                 # free commit below this = action_log line (once per 30 min)


def claude_state():
    """True = main Kai's claude.exe is alive, False = it is certainly not, None = could not tell.

    psutil reads the process list in-process: no powershell child (= no window, investigation 044)
    and no 10-second timeout that a starving PC misses. A failed read is None, never False, so
    the watchdog does not restart a healthy session just because the PC was slow to answer.
    """
    try:
        import psutil
        pids = set()
        for p in psutil.process_iter(["name", "pid"]):
            name = (p.info.get("name") or "").lower()
            if name == "claude.exe" or name == "claude":
                pids.add(p.info["pid"])
    except Exception:
        return None
    return main_claude_running(pids)


def is_claude_running() -> bool:
    """Back-compat wrapper: True only when main Kai is certainly up."""
    return claude_state() is True


def main_claude_running(claude_pids: set, bot_pids: set = None) -> bool:
    """True if a claude.exe that is NOT a domain bot session is alive.

    Multi-bot stage 1 (05/11/2026): a domain bot (config/bots.json) is its own
    claude.exe. Counting it would hide a dead main Kai from the watchdog."""
    if bot_pids is None:
        try:
            import kai_bots
            bot_pids = kai_bots.bot_claude_pids()
        except Exception:
            bot_pids = set()
    return bool(set(claude_pids) - set(bot_pids))


def send_telegram(text: str):
    """Send a Telegram message to Omer."""
    try:
        encoded = urllib.parse.quote(text)
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id={CHAT_ID}&text={encoded}"
        resp = urllib.request.urlopen(url, timeout=10)
        import tg_log  # keeps it in `messages`
        tg_log.save_response(resp, text)
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
            creationflags=CREATE_NO_WINDOW,
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
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        pass  # watchdog must never crash

    # 2. Is the RUNNING plugin process serving patched code?
    # psutil, not powershell Get-CimInstance: CIM takes minutes on this PC and the powershell child
    # opened a window every 2 minutes (investigation 044).
    cmdline = ""
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            if (p.info.get("name") or "").lower() not in ("bun.exe", "node.exe"):
                continue
            try:
                c = " ".join(p.cmdline())
            except Exception:
                continue
            if "claude-plugins-official" in c and "telegram" in c:
                cmdline = c
                break
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


# --- investigation 044: give-up cap, retry button, low-memory log ----------------------------------

def last_restart_error(path: str = RESTART_LOG) -> str:
    """The error line of the LAST run in restart.log (the wrapper mixes UTF-8 and UTF-16 output)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 60000))
            raw = f.read()
    except Exception:
        return "restart.log לא נקרא"
    text = raw.replace(b"\x00", b"").decode("utf-8", "replace")
    lines = []
    for line in text.splitlines():
        if len(line) > 6 and all(line[k] == " " for k in range(1, len(line) - 1, 2)):
            line = line[::2]  # "E R R O R" from a UTF-16 write
        line = line.strip()
        if line:
            lines.append(line)
    start = 0
    for k, line in enumerate(lines):
        if line.startswith("===== RESTART RUN 2"):
            start = k
    block = lines[start:]
    for line in reversed(block):
        if "CRASHED" in line or "ERROR" in line or "Exception" in line:
            return line.strip("= ")[:220]
    return (block[-1].strip("= ")[:220]) if block else "restart.log ריק"


def memory_line() -> str:
    try:
        import mem_guard
        st = mem_guard.commit_status()
        top = mem_guard.top_memory(3)
        if not st:
            return "לא הצלחתי לקרוא את מצב הזיכרון"
        gb = mem_guard.GB
        return (f"פנוי {st['free'] / gb:.1f}GB מתוך {st['limit'] / gb:.1f}GB. "
                f"הכי גדולים: {mem_guard.fmt_top(top)}")
    except Exception:
        return "לא הצלחתי לקרוא את מצב הזיכרון"


def check_low_memory():
    """One action_log line when free commit drops below LOW_MEMORY_GB (at most once per 30 min), so the
    next "Kai died" has its cause in the DB and not only in the Windows Event Log."""
    try:
        import mem_guard
        st = mem_guard.commit_status()
        if not st or st["free"] >= LOW_MEMORY_GB * mem_guard.GB:
            return
        conn = sqlite3.connect(DB_PATH)
        recent = conn.execute(
            "SELECT 1 FROM action_log WHERE agent='kai_watchdog' AND action='low_memory' "
            "AND created_at > datetime('now','-30 minutes') LIMIT 1").fetchone()
        conn.close()
        if not recent:
            log_to_db("low_memory", mem_guard.describe(st))
    except Exception:
        pass


def _tg_api(method: str, payload: dict, timeout: int = 10):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(req, timeout=timeout)


def send_telegram_buttons(text: str, keyboard: list):
    try:
        resp = _tg_api("sendMessage", {"chat_id": CHAT_ID, "text": text,
                                       "reply_markup": {"inline_keyboard": keyboard}})
        import tg_log  # keeps it in `messages`
        tg_log.save_response(resp, text)
    except Exception:
        pass


def main_poller_alive(bot_pids=None) -> bool:
    """True if a telegram plugin process that is NOT a domain bot's is running (or we cannot tell).

    The retry button is read with a getUpdates PEEK. That is safe only while main Kai's own poller is
    gone: two pollers on one token make Telegram answer 409 to one of them (investigation #034)."""
    try:
        import psutil
        if bot_pids is None:
            import kai_bots
            bot_pids = kai_bots.bot_claude_pids()
        for p in psutil.process_iter(["name"]):
            if (p.info.get("name") or "").lower() not in ("bun.exe", "node.exe"):
                continue
            try:
                c = " ".join(p.cmdline()).lower()
            except Exception:
                return True  # cannot read it: assume it may be ours
            if "telegram" not in c:
                continue
            try:
                parents = {x.pid for x in p.parents()}
            except Exception:
                parents = set()
            if parents & set(bot_pids):
                continue  # a domain bot's poller, other token
            return True
        return False
    except Exception:
        return True


def _load_handled() -> list:
    try:
        with open(RETRY_HANDLED_FILE, encoding="utf-8") as f:
            return list(json.load(f))
    except Exception:
        return []


def find_retry_press(updates: list, handled: list):
    """The first unhandled press of the retry button by Omer, or None."""
    for u in updates or []:
        cq = u.get("callback_query") or {}
        if cq.get("data") != RETRY_CALLBACK:
            continue
        if str((cq.get("from") or {}).get("id")) != CHAT_ID:
            continue
        if cq.get("id") in handled:
            continue
        return cq
    return None


def retry_requested() -> bool:
    """Did Omer press the retry button? Peek only: getUpdates WITHOUT offset (confirms nothing, so the
    next session still receives the update) and WITHOUT allowed_updates (that parameter is sticky and
    would change what the plugin receives, see #025)."""
    if main_poller_alive():
        return False
    try:
        resp = urllib.request.urlopen(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?timeout=0", timeout=10)
        updates = json.loads(resp.read().decode("utf-8")).get("result", [])
    except Exception:
        return False
    handled = _load_handled()
    cq = find_retry_press(updates, handled)
    if not cq:
        return False
    handled = (handled + [cq.get("id")])[-50:]
    try:
        with open(RETRY_HANDLED_FILE, "w", encoding="utf-8") as f:
            json.dump(handled, f)
    except Exception:
        pass
    try:
        _tg_api("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": "מנסה ריסטרט 🔄"})
    except Exception:
        pass
    msg = cq.get("message") or {}
    if msg.get("message_id"):
        try:
            _tg_api("editMessageText", {"chat_id": CHAT_ID, "message_id": msg["message_id"],
                                        "text": (msg.get("text") or "") + "\n‏✅ נבחר: נסה ריסטרט שוב"})
        except Exception:
            pass
    return True


def gave_up_text(attempts: int) -> str:
    return (
        f"‏❌ קאי לא רץ, ו-{attempts} ניסיונות ריסטרט נכשלו. הפסקתי לנסות כדי לא לפתוח עוד חלונות.\n\n"
        f"‏🔍 השגיאה האחרונה ב-restart.log:\n{last_restart_error()}\n\n"
        f"‏🧠 זיכרון: {memory_line()}\n\n"
        "‏לנסות ריסטרט שוב?"
    )


def main():
    # Claude is deliberately down overnight. Every check below would read that as a
    # crash: it would restart the session within two minutes and push three alerts
    # to a sleeping Omer. Stand down instead, and clear the down-cycle counter so
    # the morning's first real check starts from a clean slate rather than
    # announcing a "recovery" that was really just the scheduled startup.
    if night_mode.should_stand_down("kai_watchdog"):
        for f in (DOWN_FILE, LOCK_FILE, ATTEMPTS_FILE, GAVE_UP_FILE):
            _remove(f)
        return

    check_reaction_channel()
    check_telegram_patch()
    check_low_memory()

    state = claude_state()
    if state is None:
        # Could not read the process list (a starving PC). Unknown is not "down": no restart.
        log_to_db("check_unknown", "process list unreadable; no action this cycle")
        return

    down_cycles = _read_int(DOWN_FILE)

    if state:
        # Claude is up. If we were mid-recovery, report the recovery once, then reset.
        if down_cycles:
            log_to_db("restart_success", f"claude.exe back up after {down_cycles} down cycle(s)")
            send_telegram("✅ קאי חזר לפעול")
        for f in (DOWN_FILE, LOCK_FILE, ATTEMPTS_FILE, GAVE_UP_FILE):
            _remove(f)
        return

    # --- Claude is NOT running ---
    down_cycles += 1
    _write_int(DOWN_FILE, down_cycles)

    # Guard: a restart is already underway — do not stomp it with another one.
    # NO verdict while it runs: booting a session takes longer than one cycle.
    if restart_in_progress():
        log_to_db("restart_waiting", f"restart already in progress; down_cycles={down_cycles}")
        return

    attempts = _read_int(ATTEMPTS_FILE)
    if attempts >= MAX_RESTART_ATTEMPTS:
        # Investigation 044: 24 restarts in a row on 17/11, each crashing the same way, every 2 minutes.
        # After the cap the watchdog stops and asks ONCE; only the button starts another round.
        if retry_requested():
            log_to_db("restart_retry_button", f"Omer pressed retry; down_cycles={down_cycles}")
            attempts = 0
            _remove(GAVE_UP_FILE)
        else:
            if not os.path.exists(GAVE_UP_FILE):
                send_telegram_buttons(gave_up_text(attempts),
                                      [[{"text": "🔄 נסה ריסטרט שוב", "callback_data": RETRY_CALLBACK}]])
                _write_int(GAVE_UP_FILE, int(datetime.now().timestamp()))
                log_to_db("restart_gave_up",
                          f"attempts={attempts}; last_error={last_restart_error()}; memory={memory_line()}")
            return

    ts = datetime.now(timezone.utc).isoformat()
    _write_int(LOCK_FILE, int(datetime.now().timestamp()))
    if down_cycles == 1:
        send_telegram("⚠️ קאי לא רץ — מבצע ריסטרט אוטומטי...")
    _write_int(ATTEMPTS_FILE, attempts + 1)
    try:
        path = restart_kai()
        log_to_db("restart_attempt",
                  f"claude.exe not found at {ts} — triggered via {path}; down_cycles={down_cycles}; "
                  f"attempt={attempts + 1}/{MAX_RESTART_ATTEMPTS}")
    except Exception as e:
        _remove(LOCK_FILE)
        log_to_db("restart_failed", f"Failed to trigger restart: {e}")
        if attempts + 1 < MAX_RESTART_ATTEMPTS:
            send_telegram(f"❌ לא הצלחתי בכלל להפעיל את הריסטרט — {e}")
        # on the last attempt the give-up alert (next cycle) carries the details


if __name__ == "__main__":
    main()
