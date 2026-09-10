#!/usr/bin/env python3
"""SessionStart cleanup: delete restart sentinel message, then send 'קלוד עלה ✅'."""
import urllib.request, json, os, sqlite3, glob, signal

BOT_TOKEN = os.environ["KAI_TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Kill stale typing loops from previous session (both worker-scoped and legacy shared)
typing_pids = glob.glob('D:/Kai/tmp/typing_pid*.txt')  # note: no underscore — matches both typing_pid.txt and typing_pid_*.txt
for pid_file in typing_pids:
    try:
        with open(pid_file, 'r') as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass
    try:
        os.remove(pid_file)
    except Exception:
        pass
if typing_pids:
    print(f"Cleaned up {len(typing_pids)} stale typing loop(s)")

# A new session starts with no inherited indicator (investigation #043).
for p in ("D:/Kai/tmp/typing_lease.json", "D:/Kai/tmp/typing_resume_armed.json"):
    try:
        os.remove(p)
    except Exception:
        pass

# Also clean up any orphan typing_loop.py Python processes not tracked by a PID file
# (defense in depth — catches cases where the PID file was already removed but the process kept running)
try:
    import psutil
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline') or []
            if any('typing_loop.py' in str(a) for a in cmdline):
                proc.kill()
        except Exception:
            pass
except ImportError:
    pass

sentinel = "D:/Kai/tmp/restart_sentinel"
if os.path.exists(sentinel):
    with open(sentinel, encoding="utf-8") as f:
        msg_id = f.read().strip()
    os.remove(sentinel)
    if msg_id:
        try:
            urllib.request.urlopen(
                f"{BASE_URL}/deleteMessage?chat_id={CHAT_ID}&message_id={msg_id}",
                timeout=5
            )
        except Exception:
            pass
        try:
            conn = sqlite3.connect("D:/Kai/kai.db")
            conn.execute(
                "DELETE FROM messages WHERE telegram_message_id = ?", (int(msg_id),)
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


# Skip "קלוד עלה" for a headless child session. A batch scraper runs the
# Claude CLI once per batch of posts to parse them, and every one of those is a
# real session that fires SessionStart - so a single scrape produced a dozen
# "קלוד עלה ✅" messages. The parent sets KAI_HEADLESS_CHILD before spawning;
# nothing sets it for the interactive session, which still announces itself.
if os.environ.get("KAI_HEADLESS_CHILD"):
    print("[session_start_cleanup] Headless child session — skipping 'קלוד עלה' notification")
    raise SystemExit(0)

# Skip "קלוד עלה" if this startup was triggered by a PostCompact event (within last 3 minutes)
is_post_compact = False
try:
    conn = sqlite3.connect("D:/Kai/kai.db")
    conn.text_factory = str
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM action_log WHERE agent='on_compact' AND action='PostCompact' "
        "AND created_at > datetime('now', '-3 minutes') LIMIT 1"
    )
    is_post_compact = cur.fetchone() is not None
    conn.close()
except Exception:
    pass

if not is_post_compact:
    # Quiet hours: the night-mode restart brings Claude up at 05:00, and this message
    # used to arrive as a full push notification. Keep the message (a MISSING one is a
    # useful signal that Kai failed to come up) but send it silently between 02:00-05:30
    # Jerusalem time, so it is simply waiting in the chat by the time Omer wakes up.
    from datetime import datetime, timezone, timedelta
    _now = datetime.now(timezone(timedelta(hours=3)))
    _minutes = _now.hour * 60 + _now.minute
    quiet = 2 * 60 <= _minutes < 5 * 60 + 30
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"{BASE_URL}/sendMessage",
            json.dumps({
                "chat_id": CHAT_ID,
                "text": "קלוד עלה ✅",
                "disable_notification": quiet,
            }).encode("utf-8"),
            {"Content-Type": "application/json"}
        ), timeout=10)
    except Exception as e:
        import sys
        print(f"[session_start_cleanup] ERROR sending startup message: {e}", file=sys.stderr)
else:
    print("[session_start_cleanup] Post-compact startup detected — skipping 'קלוד עלה' notification")
