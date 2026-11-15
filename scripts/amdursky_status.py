"""Registration status of "אני גיטרה - בני אמדורסקי" (14/11/2026) -> WhatsApp group "כרטיסים ונהנים".

Sibling of registration_status.py (same sheets helpers, same bridge call, same alert path), only for
the two systems of this show, with one SHARED cap of 50 couples:
  - נשות המג"דים (self registration)   sheet 1SW8MiPx...  tab הרשמות (one row = one couple)
  - שלישים (adjutants add servicemembers) sheet 1ufCTA9V...  tab משרתים (one row = one couple), tab שלישים

Counts come from the raw rows, cross-checked against each sheet's סיכום tab. A read error or
mismatch = NO post, an alert goes to the miluim topic instead.

Usage:
  python amdursky_status.py                 print the update (no send)
  python amdursky_status.py --send          post it now
  python amdursky_status.py --install-tasks [--times 18:00,18:05,...]
  python amdursky_status.py --stop          delete the scheduled task

Task "KaiAmdurskyStatus": one trigger per slot, each with a short EndBoundary and
DeleteExpiredTaskAfter=PT0S (self-deleting). A cron run more than 4 minutes after its slot skips.
Omer approved one format (14/11/2026, msg 2894); numbers are live, no approval per post.
"""
import argparse
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime, timedelta

import warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, "D:/Kai/scripts")
import registration_status as rs  # noqa: E402

IL = rs.IL
SELF_ID = "1SW8MiPxRp8sHVIogtaB9XD6-vOF5Wy75pHh11wLpxP0"
ADJ_ID = "1ufCTA9VP5ywnG8HMjFO9bPsFtFnBHhOj6OVQMm7A0U8"
SHOW = "אני גיטרה - בני אמדורסקי 🎸"
TASK_NAME = "KaiAmdurskyStatus"
STATE = "D:/Kai/tmp/amdursky_status_state.json"
LOG = "D:/Kai/tmp/amdursky_status.log"
DEFAULT_TIMES = "18:00,18:05,18:10,18:15,18:20,18:25,18:30"


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(IL):%Y-%m-%d %H:%M:%S} {msg}\n")


def read_all():
    s = rs.sheets()
    st = rs.kv(rs.get(s, SELF_ID, "'הגדרות'!A:B"))
    regs = rs.data_rows(rs.get(s, SELF_ID, "'הרשמות'!A:F"))
    summ = rs.kv(rs.get(s, SELF_ID, "'סיכום'!A:B"))
    rs.check("נשות המג\"דים", len(regs), summ.get("נרשמו"))
    st2 = rs.kv(rs.get(s, ADJ_ID, "'הגדרות'!A:B"))
    guests = rs.data_rows(rs.get(s, ADJ_ID, "'משרתים'!A:K"), name_col=4)
    hosts = rs.data_rows(rs.get(s, ADJ_ID, "'שלישים'!A:J"))
    summ2 = rs.kv(rs.get(s, ADJ_ID, "'סיכום'!A:B"))
    rs.check("שלישים", len(guests), summ2.get("נרשמו"))
    rs.check("שלישים - שלישים", len(hosts), summ2.get("שלישים שנרשמו"))
    cap = int(st.get("מכסה משותפת לשתי המערכות") or st2.get("מכסה משותפת לשתי המערכות") or 50)
    return {"self": len(regs), "adj": len(guests), "hosts": len(hosts), "cap": cap,
            "deadline": rs.parse_deadline(st["סגירת הרשמה"])}


def build_text(d, now, final=False):
    total = d["self"] + d["adj"]
    hosts = "שליש אחד נרשם" if d["hosts"] == 1 else f"{d['hosts']} שלישים נרשמו"
    lines = [f"עדכון רישום - {SHOW} ({now:%H:%M}):", "",
             f"נשות המג\"דים - {rs.couples(d['self'])}",
             f"שלישים - {rs.couples(d['adj'])} ({hosts})", ""]
    if final or now >= d["deadline"]:
        lines.append(f"ההרשמה נסגרה ✅ סה\"כ {rs.couples(total)} ({total * 2} כרטיסים)")
    elif total >= d["cap"]:
        lines.append(f"סה\"כ {total} מתוך {d['cap']} זוגות - מלא! 🎉")
    else:
        lines.append(f"סה\"כ {total} מתוך {d['cap']} זוגות, {rs.left_text(d['cap'] - total)}.")
        lines.append(f"ההרשמה נסגרת ב-{d['deadline']:%H:%M}")
    return "\n".join(lines)


def slot_of(now, times):
    for t in times:
        h, m = map(int, t.split(":"))
        slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if timedelta(minutes=-1) <= now - slot <= timedelta(minutes=4):
            return t
    return None


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(s):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def run(send, cron, times):
    now = datetime.now(IL)
    slot = slot_of(now, times) if cron else None
    if cron:
        if slot is None:
            log(f"late run at {now:%H:%M}, skipped")
            return 0
        if load_state().get("sent", {}).get(f"{now:%Y-%m-%d} {slot}"):
            log(f"slot {slot} already sent, skipped")
            return 0
    final = bool(slot) and slot == times[-1]
    try:
        text = build_text(read_all(), now, final=final)
    except Exception as e:
        log("read failed: " + traceback.format_exc()[-1500:])
        if send:
            rs.tg_alert(f"‏⚠️ עדכון הרישום של אמדורסקי ({now:%H:%M}) לא נשלח - קריאת הגיליונות נכשלה, אז לא שלחתי מספרים.\n‏{type(e).__name__}: {str(e)[:300]}")
        print("ERROR", e)
        return 2
    print(text)
    if not send:
        return 0
    try:
        rs.wa_send(text)
    except Exception as e:
        log("send failed: " + repr(e))
        rs.tg_alert(f"‏⚠️ עדכון הרישום של אמדורסקי ({now:%H:%M}) לא נשלח - הגשר של וואטסאפ לא קיבל את ההודעה.\n‏{str(e)[:300]}")
        return 3
    log("sent:\n" + text)
    state = load_state()
    state.setdefault("sent", {})[f"{now:%Y-%m-%d} {slot or now.strftime('%H:%M')}"] = now.isoformat()
    save_state(state)
    try:
        import sqlite3
        c = sqlite3.connect("D:/Kai/kai.db")
        c.execute("INSERT INTO action_log (agent, action, details) VALUES (?,?,?)",
                  ("miluim", "amdursky_status_wa", text[:500]))
        c.commit()
        c.close()
    except Exception:
        pass
    return 0


def install(times):
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    today = datetime.now(IL).strftime("%Y-%m-%d")
    trig = []
    for t in times:
        start = f"{today}T{t}:00"
        end = (datetime.strptime(start, "%Y-%m-%dT%H:%M:%S") + timedelta(minutes=4)).strftime("%Y-%m-%dT%H:%M:%S")
        trig.append(f"    <TimeTrigger><StartBoundary>{start}</StartBoundary><EndBoundary>{end}</EndBoundary><Enabled>true</Enabled></TimeTrigger>")
    user = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")
    xml = rs.TASK_XML.format(triggers="\n".join(trig), user=user, pythonw=pyw, times=",".join(times),
                             script=os.path.abspath(__file__).replace("/", "\\"))
    xml = xml.replace("hourly registration update", "Amdursky registration update every 5 min")
    xp = "D:\\Kai\\tmp\\amdursky_status_task.xml"
    with open(xp, "w", encoding="utf-16") as f:
        f.write(xml)
    r = subprocess.run(["schtasks", "/create", "/tn", TASK_NAME, "/xml", xp, "/f"], capture_output=True, timeout=60)
    print(f"schtasks create rc={r.returncode} {r.stdout.decode('cp862', 'replace').strip()} {r.stderr.decode('cp862', 'replace').strip()}")
    return r.returncode


def stop():
    r = subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"], capture_output=True, timeout=30)
    print(f"schtasks delete rc={r.returncode} {r.stdout.decode('cp862', 'replace').strip()} {r.stderr.decode('cp862', 'replace').strip()}")
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--cron", action="store_true")
    ap.add_argument("--times", default=DEFAULT_TIMES)
    ap.add_argument("--install-tasks", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--now", help="ISO local time override for testing the text")
    ap.add_argument("--final", action="store_true", help="with --now: print the closing text")
    a = ap.parse_args()
    times = [t.strip() for t in a.times.split(",") if t.strip()]
    if a.stop:
        return stop()
    if a.install_tasks:
        return install(times)
    if a.now:
        print(build_text(read_all(), datetime.fromisoformat(a.now).replace(tzinfo=IL), final=a.final))
        return 0
    return run(a.send, a.cron, times)


if __name__ == "__main__":
    if sys.stdout is None or sys.stdout.encoding is None or sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
    sys.exit(main())
