"""
run_morning_briefing.py — Standalone deterministic morning briefing runner.

Mirrors the logic in agents/morning_briefing.md without spawning a Claude agent.
Triggered by either:
  - Manual CLI:           python D:/Kai/scripts/run_morning_briefing.py --force
  - KaiMorningBriefing task, every 30 min: sends only at LOCAL 09:xx (travel mode aware),
    once per LOCAL date. --dry-run / --now ISO_UTC to test the gate. --force skips gate + dedupe.

Sections (all skipped if empty):
  Step 0  dedup check on action_log (agent='morning_briefing', action='briefing_sent', date('now'))
  Step 1  Hebrew header (current Jerusalem date)
  Step 2  Hebcal holidays today/tomorrow (with erev awareness)
  Step 3  Open-Meteo weather (Tel Aviv, 3-day)
  Step 4  Google Calendar primary — today's events
  Step 5  DB reminders today + tomorrow
  Step 6  DB tasks today + overdue
  Step 7  DB high-priority upcoming (priority>=10, within 7 days)
  Step 8  Birthdays — people table merged with B-Days calendar
  Step 9  Unread Gmail grouped by 5 categories
  Step 10 Send via Telegram Bot API, log action_log briefing_sent

All times: UTC stored in DB -> converted to LOCAL time via scripts/local_tz.py
(Israel, or the travel-mode destination). Never hard-code UTC+3.
All Hebrew lines prefixed with RLM (U+200F) for RTL rendering.

Required pip packages:
    pip install google-api-python-client google-auth
(Open-Meteo + Hebcal + Telegram use stdlib urllib, no SDK needed.)
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
import traceback
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any

# --- Constants ---------------------------------------------------------------

KAI_ROOT = "D:/Kai"
DB_PATH = f"{KAI_ROOT}/kai.db"
LOG_PATH = f"{KAI_ROOT}/tmp/wake_trigger.log"
TOKEN_FILE = f"{KAI_ROOT}/credentials/google_token.json"
TYPING_LOOP_PATH = f"{KAI_ROOT}/scripts/typing_loop.py"
TELEGRAM_CHAT_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
BDAYS_CALENDAR_ID = "<email>"

RLM = "‏"  # RTL marker — every Hebrew line starts with this
sys.path.insert(0, os.path.join(KAI_ROOT, "scripts"))
try:
    import local_tz  # home timezone: Israel, or the travel-mode destination
    JLM = local_tz.home_tz()  # name kept for the many call sites; this is LOCAL time
except Exception:
    local_tz = None
    JLM = timezone(timedelta(hours=3))  # legacy fallback


def _mod() -> str:
    """SQLite modifier for the local offset, e.g. '+420 minutes' in Bangkok."""
    if local_tz is not None:
        try:
            return local_tz.sqlite_offset_modifier()
        except Exception:
            pass
    return "+180 minutes"

HEB_DAYS = ["יום שני", "יום שלישי", "יום רביעי", "יום חמישי", "יום שישי", "שבת", "יום ראשון"]
HEB_MONTHS = [
    "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
    "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
]

# WMO weather codes -> Hebrew short description
WMO = {
    0: "שמשי", 1: "בהיר", 2: "בהיר חלקית", 3: "מעורב",
    45: "ערפל", 48: "ערפל",
    51: "טפטוף", 53: "טפטוף", 55: "טפטוף",
    61: "גשם קל", 63: "גשם", 65: "גשם כבד",
    71: "שלג", 73: "שלג", 75: "שלג",
    80: "גשמי", 81: "גשמי", 82: "סוערים",
    95: "סערה", 96: "סערה", 99: "סערה",
}

EREV_AWARE = {
    "Yom Yerushalayim": "🏙️ הערב מתחיל יום ירושלים. שים לב: חסימות תנועה בירושלים מהצהריים, מצעד הדגלים בערב.",
    "Yom HaAtzma'ut": "🇮🇱 הערב מתחיל יום העצמאות. צפירת סיום יום הזיכרון ב-20:00, מסיבות ברחובות.",
    "Yom HaZikaron": "🕯️ הערב מתחיל יום הזיכרון. צפירה ב-20:00 (1 דקה).",
    "Yom HaShoah": "🕯️ הערב מתחיל יום הזיכרון לשואה. טקס ביד ושם 20:00.",
    "Shavuot": "🌿 הערב נכנס חג השבועות. חג שמח מחר!",
    "Pesach": "🍷 הערב נכנס חג הפסח (סדר הלילה). חג שמח!",
    "Sukkot": "🌿 הערב נכנס חג הסוכות. חג שמח מחר!",
    "Rosh Hashana": "🍎 הערב נכנס ראש השנה. שנה טובה ומתוקה!",
    "Yom Kippur": "🤍 הערב נכנס יום הכיפורים. גמר חתימה טובה!",
    "Shmini Atzeret": "🎉 הערב נכנס שמיני עצרת. חג שמח!",
}

HOLIDAY_MSG = [
    ("Yom HaShoah", "🕯️ יום הזיכרון לשואה ולגבורה - צפירה בשעה 10:00"),
    ("Yom HaZikaron", "🕯️ יום הזיכרון - צפירות: 20:00 הערב ו-11:00 מחר"),
    ("Yom HaAtzma'ut", "🇮🇱 יום עצמאות שמח!"),
    ("Yom Yerushalayim", "🏙️ יום ירושלים שמח! חסימות בירושלים."),
    ("Pesach", "🍷 חג פסח שמח!"),
    ("Shavuot", "📜 חג שבועות שמח!"),
    ("Rosh Hashana", "🍎 שנה טובה ומתוקה!"),
    ("Yom Kippur", "🤍 גמר חתימה טובה!"),
    ("Sukkot", "🌿 חג סוכות שמח!"),
    ("Shmini Atzeret", "🎉 חג שמח!"),
    ("Simchat Torah", "🎉 חג שמח!"),
    ("Chanukah", "🕎 חנוכה שמח!"),
    ("Purim", "🎭 פורים שמח!"),
    ("Lag BaOmer", "🔥 ל\"ג בעומר שמח!"),
    ("Rosh Chodesh", "🌙 ראש חודש טוב!"),
]

GMAIL_CATEGORIES = [
    ("📥 Inbox", "is:unread in:inbox category:primary"),
    ("🛍️ Promotions", "is:unread in:inbox category:promotions"),
    ("👥 Social", "is:unread in:inbox category:social"),
    ("📢 Updates", "is:unread in:inbox category:updates"),
    ("💬 Forums", "is:unread in:inbox category:forums"),
]


# --- Logging -----------------------------------------------------------------

def _log(section: str, msg: str) -> None:
    line = f"[{datetime.now(JLM).strftime('%Y-%m-%d %H:%M:%S')}] [briefing/{section}] {msg}\n"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _timed(section: str):
    """Context-manager-like helper: returns a (start_time, finalize) pair."""
    t0 = time.monotonic()
    _log(section, "start")
    def done(extra: str = "") -> None:
        dt = time.monotonic() - t0
        _log(section, f"done in {dt:.2f}s {extra}".rstrip())
    return done


# --- Telegram bot token (single source of truth: typing_loop.py) -------------

def _load_bot_token() -> str:
    """Bot token from the environment (public version)."""
    return os.environ["KAI_TELEGRAM_BOT_TOKEN"]


# --- Step 0: dedup -----------------------------------------------------------

BRIEFING_HOUR = 9  # local


def briefing_gate(now_local: datetime, already_sent: bool) -> tuple:
    """Pure decision for the scheduled run: (send?, reason)."""
    if now_local.hour != BRIEFING_HOUR:
        return False, f"not_briefing_hour (local {now_local:%H:%M})"
    if already_sent:
        return False, "already_sent_today"
    return True, "send"


def already_sent_on(conn, local_date: str, modifier: str) -> bool:
    """True if a briefing_sent row exists whose created_at (UTC) falls on local_date."""
    row = conn.execute(
        """SELECT id FROM action_log
           WHERE agent='morning_briefing' AND action='briefing_sent'
             AND date(created_at, ?) = ?
           LIMIT 1""",
        (modifier, local_date),
    ).fetchone()
    return row is not None


def _already_sent_today(now_local: datetime = None) -> bool:
    now_local = now_local or datetime.now(JLM)
    offset = now_local.utcoffset() or timedelta(hours=3)
    modifier = "%+d minutes" % int(offset.total_seconds() // 60)
    conn = sqlite3.connect(DB_PATH)
    try:
        return already_sent_on(conn, now_local.date().isoformat(), modifier)
    finally:
        conn.close()


def _mark_sent() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO action_log (agent, action, details) VALUES (?, ?, ?)",
            ("morning_briefing", "briefing_sent", f"{datetime.now(JLM).date().isoformat()} {getattr(JLM, 'key', JLM)}"),
        )
        conn.commit()
    finally:
        conn.close()


# --- Step 1: Header ----------------------------------------------------------

def _build_header() -> str:
    now = datetime.now(JLM)
    weekday_heb = HEB_DAYS[now.weekday()]
    human_date = f"{weekday_heb}, {now.day} ב{HEB_MONTHS[now.month - 1]} {now.year}"
    header = f"🌅 בוקר טוב, עומר! הנה היום שלך - {human_date}"
    label = local_tz.tz_label() if local_tz is not None else ""
    if label:
        il = datetime.now(local_tz.israel_tz())
        header += f"\n🌍 מצב חו״ל: כל השעות בשעון {label} (בישראל עכשיו {il:%H:%M})"
    return header


# --- Step 2: Hebcal holidays -------------------------------------------------

def _fetch_hebcal() -> dict[str, list[dict]]:
    today = datetime.now(JLM).date()
    tomorrow = today + timedelta(days=1)
    url = (
        "https://www.hebcal.com/hebcal?cfg=json&v=1&maj=on&min=on&mod=on&nx=on&mf=on&ss=on"
        f"&start={today}&end={tomorrow}&geo=geoname&geonameid=293397&i=on"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
    except Exception as e:
        _log("hebcal", f"error: {e}")
        return {"today": [], "tomorrow": []}
    items = data.get("items", [])
    return {
        "today": [i for i in items if i.get("date", "").startswith(str(today))
                  and i.get("category") in ("holiday", "roshchodesh")],
        "tomorrow": [i for i in items if i.get("date", "").startswith(str(tomorrow))
                     and i.get("category") in ("holiday", "roshchodesh")],
    }


def _holiday_section(hc: dict) -> list[str]:
    out: list[str] = []
    for item in hc.get("today", []):
        title = item.get("title", "")
        # "Erev X" → use EREV_AWARE map for the base holiday, not the holiday-day greeting
        if title.startswith("Erev "):
            base = title[len("Erev "):]
            for needle, msg in EREV_AWARE.items():
                if needle in base:
                    out.append(msg)
                    break
            else:
                out.append(f"📅 {title}")
            continue
        for needle, msg in HOLIDAY_MSG:
            if needle in title:
                out.append(msg)
                break
        else:
            out.append(f"📅 {title}")
    if not hc.get("today"):
        for item in hc.get("tomorrow", []):
            title = item.get("title", "")
            for needle, msg in EREV_AWARE.items():
                if needle in title:
                    out.append(msg)
                    break
    return out


# --- Step 3: Weather (Open-Meteo) --------------------------------------------

def _weather_location():
    """(lat, lon, tz_name, display_prefix). Tel Aviv at home; travel-mode weather_city abroad."""
    if local_tz is not None:
        cfg = local_tz.load_config()
        if local_tz.is_active(cfg=cfg) and cfg.get("weather_city"):
            city = cfg["weather_city"]
            geo = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
                {"name": city, "count": 1, "language": "en", "format": "json"})
            try:
                with urllib.request.urlopen(geo, timeout=10) as r:
                    res = json.loads(r.read())["results"][0]
                return res["latitude"], res["longitude"], cfg["tz"], f"{city}: "
            except Exception as e:
                _log("weather", f"geocode {city} failed: {e}")
                return None
    return 32.0853, 34.7818, "Asia/Jerusalem", ""


def _fetch_weather() -> str | None:
    loc = _weather_location()
    if loc is None:
        return None
    lat, lon, tz_name, prefix = loc
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode"
        f"&forecast_days=3&timezone={urllib.parse.quote(tz_name, safe='')}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
    except Exception as e:
        _log("weather", f"error: {e}")
        return None
    daily = data.get("daily") or {}
    if not daily.get("time"):
        return None
    mn = daily["temperature_2m_min"][0]
    mx = daily["temperature_2m_max"][0]
    wc = daily["weathercode"][0]
    precip = daily["precipitation_sum"][0] or 0
    desc = WMO.get(wc, "")
    rain = f", צפוי {precip}מ\"מ גשם" if precip > 0.5 else ""
    return f"{prefix}{desc}, {int(round(mn))}°-{int(round(mx))}°{rain}"


# --- Google APIs (Calendar + Gmail) ------------------------------------------

def _google_creds():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError as e:
        raise ImportError(
            "google-auth not installed. Run: pip install google-api-python-client google-auth"
        ) from e
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        info = json.load(f)
    creds = Credentials.from_authorized_user_info(info, scopes=info.get("scopes"))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def _gservice(name: str, version: str):
    try:
        from googleapiclient.discovery import build
    except ImportError as e:
        raise ImportError(
            "google-api-python-client not installed. Run: pip install google-api-python-client google-auth"
        ) from e
    return build(name, version, credentials=_google_creds(), cache_discovery=False)


# --- Step 4: Calendar events today -------------------------------------------

def _fetch_calendar_today() -> list[dict]:
    now = datetime.now(JLM)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    end = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
    try:
        svc = _gservice("calendar", "v3")
        resp = svc.events().list(
            calendarId="primary",
            timeMin=start,
            timeMax=end,
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute()
        return resp.get("items", [])
    except Exception as e:
        _log("calendar", f"error: {e}")
        return []


def _format_calendar(events: list[dict]) -> list[str]:
    lines = []
    for ev in events:
        summary = ev.get("summary", "(ללא כותרת)")
        start = ev.get("start", {})
        if "dateTime" in start:
            dt = datetime.fromisoformat(start["dateTime"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_local = dt.astimezone(JLM)
            lines.append(f"• {dt_local.strftime('%H:%M')} - {summary}")
        else:
            lines.append(f"• כל היום - {summary}")
    return lines


# --- Step 5: DB reminders today + tomorrow -----------------------------------

def _fetch_reminders() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = str
    try:
        cur = conn.cursor()
        m = _mod()
        cur.execute(
            """SELECT message,
                      strftime('%H:%M', trigger_at, :m) AS t,
                      date(trigger_at, :m) AS d
               FROM reminders
               WHERE status='pending'
                 AND date(trigger_at, :m) <= date('now', :m, '+1 day')
               ORDER BY trigger_at ASC""",
            {"m": m},
        )
        return [{"message": r[0], "time": r[1], "date": r[2]} for r in cur.fetchall()]
    finally:
        conn.close()


# --- Step 6: DB tasks today + overdue ----------------------------------------

def _fetch_tasks() -> tuple[list[dict], list[dict]]:
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = str
    try:
        cur = conn.cursor()
        m = _mod()
        cur.execute(
            """SELECT t.title, t.priority, d.name,
                      strftime('%H:%M', t.due_at, :m)
               FROM tasks t LEFT JOIN domains d ON t.domain_id=d.id
               WHERE t.status='open'
                 AND date(t.due_at, :m) = date('now', :m)
               ORDER BY t.priority DESC""",
            {"m": m},
        )
        today = [{"title": r[0], "priority": r[1], "domain": r[2], "due": r[3]} for r in cur.fetchall()]
        cur.execute(
            """SELECT title, priority,
                      CAST(julianday('now')-julianday(due_at) AS INTEGER)
               FROM tasks
               WHERE status='open' AND due_at<datetime('now')
               ORDER BY priority DESC"""
        )
        overdue = [{"title": r[0], "priority": r[1], "days": r[2]} for r in cur.fetchall()]
        return today, overdue
    finally:
        conn.close()


# --- Step 7: DB high-priority upcoming ---------------------------------------

def _fetch_high_pri_upcoming() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = str
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT title, priority,
                      CAST(julianday(due_at)-julianday('now') AS INTEGER)
               FROM tasks
               WHERE status='open' AND priority>=10 AND due_at IS NOT NULL
                 AND due_at>datetime('now')
                 AND (julianday(due_at)-julianday('now'))<=7
               ORDER BY due_at ASC"""
        )
        return [{"title": r[0], "priority": r[1], "days": r[2]} for r in cur.fetchall()]
    finally:
        conn.close()


# --- Step 8: Birthdays (people + B-Days calendar) ---------------------------

def _days_until_birthday(bday: str, today: date) -> int | None:
    """Whole days from LOCAL `today` to the next occurrence of birthday MM-DD.

    Pure date-vs-date arithmetic (never date-vs-datetime, never UTC 'now'):
    0 = today, and a birthday already past this year rolls to next year.
    """
    try:
        month, day = int(bday[5:7]), int(bday[8:10])
    except (ValueError, IndexError):
        return None
    for year in (today.year, today.year + 1):
        try:
            nxt = date(year, month, day)
        except ValueError:  # 29/02 on a non-leap year -> 01/03
            nxt = date(year, 3, 1)
        if nxt >= today:
            return (nxt - today).days
    return None


def _fetch_people_birthdays() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = str
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, name, birthday, remind_birthday_days
               FROM people
               WHERE birthday IS NOT NULL AND length(birthday) >= 10"""
        )
        rows = cur.fetchall()
        aliases: dict[int, list[str]] = {}
        for pid, nick in cur.execute("SELECT person_id, nickname FROM people_nicknames"):
            aliases.setdefault(pid, []).append(nick)
        out: list[dict] = []
        today = datetime.now(JLM).date()  # LOCAL calendar date (travel-mode aware)
        for pid, name, bday, remind in rows:
            days = _days_until_birthday(bday, today)
            if days is None:
                continue
            if 0 <= days <= (remind if remind is not None else 3):
                out.append({"name": name, "birthday": bday, "remind_days": remind, "days_until": days,
                            "aliases": aliases.get(pid, [])})
        out.sort(key=lambda x: x["days_until"])
        return out
    finally:
        conn.close()


def _fetch_bdays_calendar() -> list[dict]:
    now = datetime.now(JLM)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    end = (now + timedelta(days=30)).replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
    try:
        svc = _gservice("calendar", "v3")
        resp = svc.events().list(
            calendarId=BDAYS_CALENDAR_ID,
            timeMin=start, timeMax=end,
            singleEvents=True, orderBy="startTime",
            maxResults=100,
        ).execute()
        items = resp.get("items", [])
    except Exception as e:
        _log("bdays-cal", f"error: {e}")
        return []
    today = now.date()
    out = []
    for ev in items:
        name = ev.get("summary", "")
        s = ev.get("start", {})
        d_str = s.get("date") or (s.get("dateTime", "")[:10])
        if not d_str:
            continue
        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        days = (d - today).days
        # Google can return yesterday's all-day event when the calendar's own
        # timezone is ahead/behind the local one (travel mode). A birthday that
        # already passed is not upcoming - drop it instead of showing "-1 ימים".
        if days < 0:
            continue
        out.append({"name": name, "date": d_str, "days_until": days})
    return out


def _merge_birthdays(people: list[dict], bdays: list[dict]) -> list[dict]:
    def tokens(name: str) -> set[str]:
        # vav/yod dropped so "אמא" (calendar) == "אימא" (nickname); a match also needs the same MM-DD.
        words = name.replace('"', "").replace("׳", "").split()
        return {t.replace("ו", "").replace("י", "") for t in words if len(t) >= 2}

    merged: list[dict] = []
    seen: list[tuple[str, set[str]]] = []
    for p in people:
        mm_dd = p["birthday"][5:] if p.get("birthday") else ""
        toks = tokens(p["name"])
        for alias in p.get("aliases", []):   # the calendar may list mom as "אמא", not "<contact> "
            toks |= tokens(alias)
        seen.append((mm_dd, toks))
        merged.append({"name": p["name"], "days_until": p["days_until"], "source": "people"})
    for b in bdays:
        mm_dd = b["date"][5:]
        toks = tokens(b["name"])
        if any(mm_dd == sm and toks & st for sm, st in seen):
            continue
        merged.append({"name": b["name"], "days_until": b["days_until"], "source": "calendar"})
    merged.sort(key=lambda x: x["days_until"])
    # Safety net: only upcoming birthdays (0 = today), never a negative count.
    return [m for m in merged if 0 <= m["days_until"] <= 14]


# --- Step 9: Gmail unread by category ----------------------------------------

def _fetch_gmail_categories() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {label: [] for label, _ in GMAIL_CATEGORIES}
    try:
        svc = _gservice("gmail", "v1")
    except ImportError as e:
        _log("gmail", f"import error: {e}")
        return out
    except Exception as e:
        _log("gmail", f"auth error: {e}")
        return out
    for label, query in GMAIL_CATEGORIES:
        try:
            resp = svc.users().messages().list(userId="me", q=query, maxResults=10).execute()
            msgs = resp.get("messages", [])
            details = []
            for m in msgs:
                try:
                    md = svc.users().messages().get(
                        userId="me", id=m["id"],
                        format="metadata",
                        metadataHeaders=["From", "Subject"],
                    ).execute()
                    headers = {h["name"]: h["value"] for h in md.get("payload", {}).get("headers", [])}
                    sender = headers.get("From", "?")
                    subject = headers.get("Subject", "(ללא נושא)")
                    # Strip email address, keep display name
                    if "<" in sender:
                        sender = sender.split("<")[0].strip().strip('"')
                    details.append({"sender": sender, "subject": subject})
                except Exception as e:
                    _log("gmail", f"msg fetch {m['id']}: {e}")
            out[label] = details
        except Exception as e:
            _log("gmail", f"{label} list error: {e}")
    return out


# --- Compose -----------------------------------------------------------------

def _rlm_lines(lines: list[str]) -> str:
    """Join lines, prefixing each non-empty line with RLM for RTL rendering."""
    out = []
    for ln in lines:
        if ln == "":
            out.append("")
        else:
            out.append(RLM + ln)
    return "\n".join(out)


def _compose(parts: dict[str, Any]) -> str:
    L: list[str] = []
    L.append(parts["header"])
    L.append("")

    if parts.get("holidays"):
        for h in parts["holidays"]:
            L.append(h)
        L.append("")

    if parts.get("weather"):
        L.append("🌤️ מזג אוויר")
        L.append(parts["weather"])
        L.append("")

    if parts.get("cal_lines"):
        L.append("📅 יומן")
        L.extend(parts["cal_lines"])
        L.append("")

    rems = parts.get("reminders") or []
    if rems:
        L.append("⏰ תזכורות")
        today_iso = datetime.now(JLM).date().isoformat()
        for r in rems:
            tag = "" if r["date"] == today_iso else " (מחר)"
            L.append(f"• {r['time']} - {r['message']}{tag}")
        L.append("")

    tasks_today, overdue = parts.get("tasks_today") or [], parts.get("overdue") or []
    if tasks_today or overdue:
        L.append("📋 משימות להיום")
        for t in tasks_today:
            dom = f" ({t['domain']})" if t.get("domain") else ""
            tm = f" {t['due']}" if t.get("due") else ""
            L.append(f"• [P{t['priority']}]{tm} {t['title']}{dom}")
        for t in overdue:
            L.append(f"• [P{t['priority']}] {t['title']} - {t['days']} ימים באיחור")
        L.append("")

    hp = parts.get("high_pri_upcoming") or []
    if hp:
        L.append("⚠️ בעדיפות גבוהה - קרוב")
        for t in hp:
            L.append(f"• [P{t['priority']}] {t['title']} - בעוד {t['days']} ימים")
        L.append("")

    bdays = parts.get("birthdays") or []
    if bdays:
        L.append("🎂 ימי הולדת")
        for b in bdays:
            if b["days_until"] == 0:
                L.append(f"• היום: {b['name']} 🎉")
            else:
                L.append(f"• בעוד {b['days_until']} ימים: {b['name']}")
        L.append("")

    gmail = parts.get("gmail") or {}
    has_mail = any(gmail.get(k) for k in gmail)
    if has_mail:
        L.append("📧 מיילים שלא נקראו")
        L.append("")
        for label, _q in GMAIL_CATEGORIES:
            msgs = gmail.get(label) or []
            if not msgs:
                continue
            L.append(f"{label} ({len(msgs)})")
            for m in msgs:
                L.append(f"• {m['sender']} - \"{m['subject']}\"")
            L.append("")

    # Trim trailing blank lines
    while L and L[-1] == "":
        L.pop()
    return _rlm_lines(L)


# --- Send --------------------------------------------------------------------

def _telegram_send(text: str) -> dict:
    token = _load_bot_token()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


# --- Public entry point ------------------------------------------------------

def run_briefing(force: bool = False, dry_run: bool = False, now_utc: datetime = None) -> dict:
    """Build and send today's briefing. Returns a result dict.

    Scheduled runs (every 30 min) pass the local-hour gate and the local-date dedupe;
    --force skips both (manual / on-demand). --dry-run reports the decision and sends nothing.
    """
    started = datetime.now(JLM)
    try:
        if not force:
            now_local = (now_utc or datetime.now(timezone.utc)).astimezone(JLM)
            ok, reason = briefing_gate(now_local, _already_sent_today(now_local) if now_local.hour == BRIEFING_HOUR else False)
            if dry_run:
                return {"ok": True, "dry_run": True, "would_send": ok, "reason": reason,
                        "local": now_local.isoformat(), "tz": str(getattr(JLM, "key", JLM))}
            if not ok:
                if reason == "already_sent_today":
                    _log("dedup", "already_sent — skipping")
                return {"ok": True, "skipped": reason}
        elif dry_run:
            return {"ok": True, "dry_run": True, "would_send": True, "reason": "force"}
        _log("run", f"start force={force} tz={getattr(JLM, 'key', JLM)}")

        done = _timed("header"); header = _build_header(); done()
        done = _timed("hebcal"); hc = _fetch_hebcal(); done()
        holidays = _holiday_section(hc)
        done = _timed("weather"); weather = _fetch_weather(); done()
        done = _timed("calendar"); cal_events = _fetch_calendar_today(); done(f"({len(cal_events)} events)")
        cal_lines = _format_calendar(cal_events)
        done = _timed("reminders"); reminders = _fetch_reminders(); done(f"({len(reminders)})")
        done = _timed("tasks"); tasks_today, overdue = _fetch_tasks(); done(f"({len(tasks_today)}/{len(overdue)})")
        done = _timed("high_pri"); high_pri = _fetch_high_pri_upcoming(); done(f"({len(high_pri)})")
        done = _timed("people_bdays"); people_bd = _fetch_people_birthdays(); done(f"({len(people_bd)})")
        done = _timed("cal_bdays"); cal_bd = _fetch_bdays_calendar(); done(f"({len(cal_bd)})")
        birthdays = _merge_birthdays(people_bd, cal_bd)
        done = _timed("gmail"); gmail = _fetch_gmail_categories(); done(
            "(" + ",".join(f"{k}:{len(v)}" for k, v in gmail.items()) + ")"
        )

        text = _compose({
            "header": header,
            "holidays": holidays,
            "weather": weather,
            "cal_lines": cal_lines,
            "reminders": reminders,
            "tasks_today": tasks_today,
            "overdue": overdue,
            "high_pri_upcoming": high_pri,
            "birthdays": birthdays,
            "gmail": gmail,
        })

        done = _timed("send"); resp = _telegram_send(text); done()
        if not resp.get("ok"):
            _log("send", f"telegram error: {resp}")
            return {"ok": False, "error": f"telegram: {resp}"}

        _mark_sent()
        elapsed = (datetime.now(JLM) - started).total_seconds()
        _log("run", f"complete in {elapsed:.2f}s")
        return {"ok": True, "elapsed_s": elapsed, "message_id": resp.get("result", {}).get("message_id")}
    except Exception as e:
        tb = traceback.format_exc()
        _log("run", f"FATAL: {e}\n{tb}")
        return {"ok": False, "error": str(e), "traceback": tb}


# --- CLI ---------------------------------------------------------------------

if __name__ == "__main__":
    force = "--force" in sys.argv
    dry = "--dry-run" in sys.argv
    now_arg = None
    if "--now" in sys.argv:
        now_arg = datetime.fromisoformat(sys.argv[sys.argv.index("--now") + 1].replace("Z", "+00:00"))
        if now_arg.tzinfo is None:
            now_arg = now_arg.replace(tzinfo=timezone.utc)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    res = run_briefing(force=force, dry_run=dry, now_utc=now_arg)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res.get("ok") else 1)
