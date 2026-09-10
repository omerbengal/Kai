#!/usr/bin/env python3
"""
connect_watcher.py — Investigation #037 controlled experiment.

Runs OUTSIDE Claude Code, survives a restart. Watches the Claude-Code-side
telegram plugin MCP log dir for the NEXT session's log file, then measures the
gap between session start (log file creation) and the first
"Starting connection" / "Successfully connected" event.

It pings Omer DIRECTLY via the Telegram Bot API (sendMessage only — this does
NOT touch getUpdates, so it never competes with the plugin poller for the
single getUpdates slot). This gives a channel-independent feedback path so Omer
is never stranded while we run the no-reload experiment.

Purpose: answer the one open question — does Claude Code EVER auto-connect the
telegram plugin channel after a restart, or ONLY when the user runs
/reload-plugins? If a "Starting connection" appears with NO reload, it connected
on its own (record the gap). If 120s pass with none, that proves CC does not
auto-connect and Omer should reload (safety net); we still capture the eventual
reload-connect for completeness.
"""

import os
import sys
import time
import glob
import json
import urllib.request
import urllib.parse
from datetime import datetime

TOKEN = os.environ["KAI_TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
LOG_DIR = os.path.join(
    os.environ["LOCALAPPDATA"],
    "claude-cli-nodejs", "Cache", "D--Kai",
    "mcp-logs-plugin-telegram-telegram",
)
RESULT_LOG = "D:/Kai/tmp/connect_experiment.log"

NO_CONNECT_DEADLINE = 120  # seconds after new session detected
MAX_RUNTIME = 420          # hard stop so the watcher never zombies


def notify(text):
    """Send a Telegram message via direct Bot API (sendMessage only)."""
    try:
        data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=data, timeout=10
        )
    except Exception as e:
        rlog(f"notify failed: {e}")


def rlog(msg):
    line = f"{datetime.now().isoformat(timespec='seconds')}  {msg}"
    try:
        with open(RESULT_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def list_logs():
    return set(glob.glob(os.path.join(LOG_DIR, "*.jsonl")))


def read_connect_events(path):
    """Return (starting_ts, connected_ts) ISO strings if present, else (None, None)."""
    starting = connected = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                dbg = ev.get("debug", "")
                if starting is None and "Starting connection" in dbg:
                    starting = ev.get("timestamp")
                if connected is None and "Successfully connected" in dbg:
                    connected = ev.get("timestamp")
        return starting, connected
    except Exception:
        return None, None


def main():
    os.makedirs(os.path.dirname(RESULT_LOG), exist_ok=True)
    rlog("=== connect_watcher started (waiting for new session log file) ===")
    start_wall = time.time()

    baseline = list_logs()
    rlog(f"baseline log files: {len(baseline)}")

    new_file = None
    # Phase 1: wait for a NEW jsonl file to appear (the next session's log).
    while time.time() - start_wall < MAX_RUNTIME:
        current = list_logs()
        fresh = current - baseline
        if fresh:
            new_file = max(fresh, key=os.path.getctime)
            break
        time.sleep(2)

    if not new_file:
        rlog("no new session log appeared before MAX_RUNTIME; exiting")
        notify("🔬 connect_watcher: לא זוהה סשן חדש בזמן. הניסוי לא רץ.")
        return

    created = os.path.getctime(new_file)
    created_iso = datetime.fromtimestamp(created).isoformat(timespec="seconds")
    rlog(f"NEW session log detected: {os.path.basename(new_file)} (created {created_iso})")
    notify(
        "🔬 הניסוי התחיל — זיהיתי סשן חדש שעלה.\n"
        "אל תיגע ב-reload-plugins. שלח 'טסט' וחכה.\n"
        "אני צופה אם קלוד מתחבר לערוץ לבד..."
    )

    detected = time.time()
    warned = False
    # Phase 2: watch for connection events.
    while time.time() - start_wall < MAX_RUNTIME:
        starting, connected = read_connect_events(new_file)
        if connected:
            # gap from session-start (file creation) to successful connect
            try:
                c = datetime.fromisoformat(connected.replace("Z", "+00:00")).timestamp()
                gap = c - created
            except Exception:
                gap = -1
            reloaded = warned  # if we already warned, this connect followed a manual reload
            tag = "אחרי reload ידני" if reloaded else "לבד, בלי reload ✅"
            rlog(f"CONNECTED. starting={starting} connected={connected} gap={gap:.1f}s reloaded_first={reloaded}")
            notify(
                f"{'✅' if not reloaded else '📍'} קלוד התחבר לערוץ ({tag}).\n"
                f"פער מעליית הסשן עד חיבור: {gap:.0f} שניות.\n"
                f"רשמתי ל-#037. עכשיו יש קשר — אפשר להמשיך."
            )
            return

        if not warned and time.time() - detected > NO_CONNECT_DEADLINE:
            warned = True
            rlog(f"NO connection after {NO_CONNECT_DEADLINE}s — instructing manual reload")
            notify(
                f"❌ {NO_CONNECT_DEADLINE} שניות ואין חיבור לבד.\n"
                "זו ההוכחה: קלוד-קוד לא מתחבר לערוץ לבד אחרי ריסטרט.\n"
                "עשה עכשיו /reload-plugins ידני — זה יחזיר קשר מיד. אני עדיין צופה ואמדוד את זמן החיבור אחרי ה-reload."
            )
        time.sleep(2)

    rlog("MAX_RUNTIME reached without a successful-connect event; exiting")
    notify("🔬 connect_watcher: נגמר זמן הצפייה. בדוק ידנית את הלוג אם צריך.")


if __name__ == "__main__":
    main()
