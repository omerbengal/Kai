"""
Fast tools for the live voice agent.

Direct kai.db / Google Calendar calls, no LLM and no Telegram side effects.
Reuses the shared helpers from scripts/fast/_base.py, scripts/fast/_gcal.py and
scripts/local_tz.py. The fast_*.py scripts themselves are NOT spawned: they send
Telegram replies and manage ACKs, which the voice path must not do.

Every function returns a plain JSON-serialisable dict, which is what the Gemini
Live session receives as the function response. All times the model sees are
Omer's LOCAL time (Israel, or the travel-mode destination). DB times are UTC with
a space separator ('YYYY-MM-DD HH:MM:SS').
"""
import re
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "D:/Kai/scripts")
sys.path.insert(0, "D:/Kai/scripts/fast")

import local_tz  # noqa: E402
import threading  # noqa: E402
from _base import get_db, action_log as _action_log_sync  # noqa: E402


def action_log(agent, action, details):
    """Audit log off the hot path: a second commit costs ~100ms on this disk."""
    threading.Thread(target=_action_log_sync, args=(agent, action, details), daemon=True).start()

AGENT = "voice"
VALID_RECURRENCE = {"daily", "weekly", "monthly", "yearly"}
DOMAIN_ALIASES = {
    "עבודה": "work", "אישי": "personal", "בריאות": "health", "כספים": "finance",
    "משפחה": "family", "חברים": "friends", "תחביבים": "hobbies", "קאי": "Project: Kai",
}


# ---------------------------------------------------------------- helpers

def _local(utc_value):
    """UTC DB value -> 'YYYY-MM-DD HH:MM' local (with a travel label when abroad)."""
    if not utc_value:
        return None
    try:
        return local_tz.fmt_local(utc_value, "%Y-%m-%d %H:%M")
    except Exception:
        return str(utc_value)[:16]


def _utc_now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _to_utc(local_str):
    """'YYYY-MM-DD HH:MM' local -> UTC DB string. Raises ValueError on bad input."""
    return local_tz.local_str_to_utc_str(local_str)


def now_info():
    now = local_tz.now_local()
    return {
        "now_local": now.strftime("%Y-%m-%d %H:%M"),
        "weekday": now.strftime("%A"),
        "timezone": local_tz.home_tz_name(),
        "travel_label": local_tz.tz_label() or None,
    }


def _reminder_row(r):
    return {"id": r["id"], "message": r["message"], "when_local": _local(r["trigger_at"]),
            "recurrence": r["recurrence"]}


def _task_row(r):
    return {"id": r["id"], "title": r["title"], "priority": r["priority"],
            "domain": r["domain"], "due_local": _local(r["due_at"])}


def _find_pending_reminders(conn, reminder_id=None, keyword=None):
    if reminder_id is not None:
        return conn.execute(
            "SELECT id, message, trigger_at, recurrence FROM reminders WHERE status='pending' AND id=?",
            (int(reminder_id),)).fetchall()
    if keyword:
        return conn.execute(
            "SELECT id, message, trigger_at, recurrence FROM reminders WHERE status='pending' "
            "AND message LIKE ? ORDER BY trigger_at ASC LIMIT 6", (f"%{keyword}%",)).fetchall()
    return []


def _find_open_tasks(conn, task_id=None, keyword=None):
    base = ("SELECT t.id, t.title, t.priority, d.name AS domain, t.due_at FROM tasks t "
            "LEFT JOIN domains d ON t.domain_id = d.id WHERE t.status='open' ")
    if task_id is not None:
        return conn.execute(base + "AND t.id=?", (int(task_id),)).fetchall()
    if keyword:
        return conn.execute(base + "AND t.title LIKE ? ORDER BY t.priority DESC LIMIT 6",
                            (f"%{keyword}%",)).fetchall()
    return []


def _resolve_one(rows, row_fn, what):
    """Return (row, error_dict). Voice must never guess between several matches."""
    if not rows:
        return None, {"ok": False, "error": f"no matching {what} found"}
    if len(rows) > 1:
        return None, {"ok": False, "error": "ambiguous", "candidates": [row_fn(r) for r in rows],
                      "hint": "ask Omer which one, then call again with the id"}
    return rows[0], None


# ---------------------------------------------------------------- reminders

def list_reminders(days=30):
    days = int(days or 30)
    cutoff = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, message, trigger_at, recurrence FROM reminders WHERE status='pending' "
            "AND trigger_at <= ? ORDER BY trigger_at ASC LIMIT 25", (cutoff,)).fetchall()
    finally:
        conn.close()
    return {"ok": True, "count": len(rows), "days": days, "reminders": [_reminder_row(r) for r in rows]}


def create_reminder(message, when_local, recurrence=None):
    message = (message or "").strip()
    if not message:
        return {"ok": False, "error": "message is empty"}
    try:
        trigger_at = _to_utc(when_local)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if recurrence in ("", "none", None):
        recurrence = None
    elif recurrence not in VALID_RECURRENCE:
        return {"ok": False, "error": f"recurrence must be one of {sorted(VALID_RECURRENCE)}"}
    warning = "time is in the past" if trigger_at < _utc_now_str() else None
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO reminders (message, trigger_at, recurrence, status) VALUES (?, ?, ?, 'pending')",
            (message, trigger_at, recurrence))
        conn.commit()
        rid = cur.lastrowid
    finally:
        conn.close()
    action_log(AGENT, "reminder_created", f'id={rid} "{message}" at {trigger_at} UTC')
    out = {"ok": True, "id": rid, "message": message, "when_local": _local(trigger_at), "recurrence": recurrence}
    if warning:
        out["warning"] = warning
    return out


def reschedule_reminder(new_when_local=None, reminder_id=None, keyword=None, new_message=None):
    if not new_when_local and not new_message:
        return {"ok": False, "error": "nothing to change: give new_when_local and/or new_message"}
    new_utc = None
    if new_when_local:
        try:
            new_utc = _to_utc(new_when_local)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
    conn = get_db()
    try:
        row, err = _resolve_one(_find_pending_reminders(conn, reminder_id, keyword), _reminder_row, "pending reminder")
        if err:
            return err
        sets, params = [], []
        if new_utc:
            sets.append("trigger_at=?"); params.append(new_utc)
        if new_message:
            sets.append("message=?"); params.append(new_message.strip())
        params.append(row["id"])
        conn.execute(f"UPDATE reminders SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
        new = conn.execute("SELECT id, message, trigger_at, recurrence FROM reminders WHERE id=?",
                           (row["id"],)).fetchone()
    finally:
        conn.close()
    action_log(AGENT, "reminder_rescheduled", f"id={row['id']} time={new_utc} msg_changed={bool(new_message)}")
    return {"ok": True, "before": _reminder_row(row), "after": _reminder_row(new)}


def cancel_reminder(reminder_id=None, keyword=None):
    """Soft cancel (status='dismissed'), same as fast_reminder_cancel.py."""
    conn = get_db()
    try:
        row, err = _resolve_one(_find_pending_reminders(conn, reminder_id, keyword), _reminder_row, "pending reminder")
        if err:
            return err
        conn.execute("UPDATE reminders SET status='dismissed' WHERE id=?", (row["id"],))
        conn.commit()
    finally:
        conn.close()
    action_log(AGENT, "reminder_cancelled", f"id={row['id']} {row['message']}")
    return {"ok": True, "cancelled": _reminder_row(row)}


# ---------------------------------------------------------------- tasks

def list_tasks(filter="all"):
    filter = filter or "all"
    base = ("SELECT t.id, t.title, t.priority, d.name AS domain, t.due_at FROM tasks t "
            "LEFT JOIN domains d ON t.domain_id = d.id WHERE t.status='open' ")
    conn = get_db()
    try:
        if filter == "today":
            now = local_tz.now_local()
            start = local_tz.utc_db_str(now.replace(hour=0, minute=0, second=0, microsecond=0))
            end = local_tz.utc_db_str(now.replace(hour=23, minute=59, second=59, microsecond=0))
            rows = conn.execute(base + "AND t.due_at BETWEEN ? AND ? ORDER BY t.priority DESC", (start, end)).fetchall()
        elif filter == "overdue":
            rows = conn.execute(base + "AND t.due_at IS NOT NULL AND t.due_at < ? ORDER BY t.priority DESC",
                                (_utc_now_str(),)).fetchall()
        else:
            filter = "all"
            rows = conn.execute(base + "ORDER BY t.priority DESC, t.due_at ASC LIMIT 40").fetchall()
    finally:
        conn.close()
    return {"ok": True, "filter": filter, "count": len(rows), "tasks": [_task_row(r) for r in rows]}


def create_task(title, priority=5, domain=None, due_local=None, notes=None):
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "title is empty"}
    try:
        priority = max(0, min(11, int(priority if priority is not None else 5)))
    except (TypeError, ValueError):
        priority = 5
    due_at = None
    if due_local:
        try:
            due_at = _to_utc(due_local)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
    conn = get_db()
    try:
        domain_id, domain_name = None, None
        if domain:
            d = DOMAIN_ALIASES.get(domain.strip(), domain.strip())
            row = conn.execute("SELECT id, name FROM domains WHERE lower(name)=lower(?) OR lower(name) LIKE lower(?)",
                               (d, f"%{d}%")).fetchone()
            if row:
                domain_id, domain_name = row["id"], row["name"]
        cur = conn.execute(
            "INSERT INTO tasks (title, notes, priority, domain_id, due_at, status) VALUES (?, ?, ?, ?, ?, 'open')",
            (title, notes, priority, domain_id, due_at))
        conn.commit()
        tid = cur.lastrowid
    finally:
        conn.close()
    action_log(AGENT, "task_created", f'id={tid} "{title}" P{priority}')
    return {"ok": True, "id": tid, "title": title, "priority": priority, "domain": domain_name,
            "due_local": _local(due_at)}


def complete_task(task_id=None, keyword=None):
    conn = get_db()
    try:
        row, err = _resolve_one(_find_open_tasks(conn, task_id, keyword), _task_row, "open task")
        if err:
            return err
        conn.execute("UPDATE tasks SET status='done', completed_at=? WHERE id=?", (_utc_now_str(), row["id"]))
        conn.commit()
    finally:
        conn.close()
    action_log(AGENT, "task_done", f"id={row['id']} {row['title']}")
    return {"ok": True, "done": _task_row(row)}


# ---------------------------------------------------------------- calendar

_cal_service = None


def _calendar():
    global _cal_service
    if _cal_service is None:
        from _gcal import get_calendar_service
        _cal_service = get_calendar_service()
    return _cal_service


_cal_list, _cal_list_at = None, 0.0


def _visible_calendars():
    """The calendars Omer has ticked in Google Calendar (not only primary), cached 10 minutes."""
    global _cal_list, _cal_list_at
    if _cal_list is None or time.time() - _cal_list_at > 600:
        items = _calendar().calendarList().list().execute().get("items", [])
        _cal_list = [(c["id"], "ראשי" if c.get("primary") else c.get("summaryOverride") or c.get("summary", ""))
                     for c in items if c.get("selected") or c.get("primary")]
        _cal_list_at = time.time()
    return _cal_list


def _event_row(ev):
    s, e = ev.get("start", {}), ev.get("end", {})
    tz = local_tz.home_tz()
    if "dateTime" in s:
        st = datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00")).astimezone(tz)
        en = datetime.fromisoformat(e["dateTime"].replace("Z", "+00:00")).astimezone(tz) if "dateTime" in e else None
        start, end, all_day = st.strftime("%Y-%m-%d %H:%M"), en.strftime("%Y-%m-%d %H:%M") if en else None, False
    else:
        start, end, all_day = s.get("date"), e.get("date"), True
    me = next((a for a in ev.get("attendees", []) if a.get("self")), None)
    return {"title": ev.get("summary", "(ללא כותרת)"), "start_local": start, "end_local": end,
            "all_day": all_day, "location": ev.get("location") or None,
            "my_response": "organizer" if ev.get("organizer", {}).get("self") else (me or {}).get("responseStatus")}


# Hebrew/spoken names -> a substring of the real calendar name (a literal filter on the spoken name
# returned nothing on 25/09, so spoken aliases map to the calendar's real name).
CALENDAR_ALIASES = {
    "משמרות": "shifts", "shifts": "shifts",
    "ימי הולדת": "b-days", "יום הולדת": "b-days", "birthdays": "b-days",
    "עבודה": "work", "work": "work", "<partner>": "<partner-calendar>", "ראשי": "ראשי", "main": "ראשי", "primary": "ראשי",
}


def _plain(text):
    """Lowercase and drop Hebrew niqqud, so "דָּנָה" matches "דנה"."""
    return re.sub("[֑-ׇ]", "", text).strip().lower()


def _calendar_names_matching(query):
    q = _plain(query)
    keys = {q} | {_plain(v) for k, v in CALENDAR_ALIASES.items() if k in q or q in k}
    return {name for _, name in _visible_calendars() if any(k and k in _plain(name) for k in keys)}


PREP_SUFFIX = " - הכנות"


def _merge_report_times(events):
    """Fold each 'X - הכנות' into the same-day 'X' event as report_local, so one shift is one
    item (on 25/09 the raw pairs made Kai say "10 activities, here are the first 5" for 5 shifts)."""
    mains = {(r["calendar"], (r["start_local"] or "")[:10], r["title"]): r for r in events
             if not r["title"].endswith(PREP_SUFFIX)}
    out = []
    for r in events:
        if r["title"].endswith(PREP_SUFFIX):
            main = mains.get((r["calendar"], (r["start_local"] or "")[:10], r["title"][:-len(PREP_SUFFIX)]))
            if main is not None:
                main["report_local"] = r["start_local"]
                continue
        out.append(r)
    return out


HEB_DAYS = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]  # datetime.weekday() order


def get_calendar(range="today", days=7, start_date=None, end_date=None, calendar=None):
    """range='today' | 'tomorrow' | 'upcoming' (from now for `days` days) | 'dates' (start_date..end_date
    inclusive, 'YYYY-MM-DD'). `calendar` keeps only calendars whose name contains it."""
    tz = local_tz.home_tz()
    now = datetime.now(tz)
    if range == "dates" or start_date:
        range = "dates"
        try:
            t_min = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=tz)
            t_max = datetime.strptime(end_date or start_date, "%Y-%m-%d").replace(tzinfo=tz) + timedelta(days=1)
        except (TypeError, ValueError):
            return {"ok": False, "error": "start_date/end_date must be YYYY-MM-DD"}
        if t_max <= t_min or (t_max - t_min).days > 62:
            return {"ok": False, "error": "date range must be 1-62 days"}
    elif range == "today":
        t_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
        t_max = t_min + timedelta(days=1)
    elif range == "tomorrow":
        t_min = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        t_max = t_min + timedelta(days=1)
    else:
        range = "upcoming"
        t_min, t_max = now, now + timedelta(days=max(1, min(31, int(days or 7))))
    for attempt in (1, 2):
        try:
            events, failed = _events_all_calendars(t_min, t_max)
            break
        except Exception as e:
            global _cal_service, _cal_list
            _cal_service = _cal_list = None  # token may have expired; rebuild once
            if attempt == 2:
                return {"ok": False, "error": f"calendar error: {e}"}
    note = None
    if calendar:
        wanted = _calendar_names_matching(calendar)
        if wanted:
            events = [r for r in events if r.get("calendar") in wanted]
        else:
            # A wrong filter must never read as "no events" - fall back to everything and say so.
            note = f"no calendar matches '{calendar}'; showing all calendars"
    events.sort(key=lambda r: (r["start_local"] or "", r["all_day"] is False))
    events = _merge_report_times(events)[:40]
    out = {"ok": True, "range": range, "from_local": t_min.strftime("%Y-%m-%d %H:%M"),
           "to_local": t_max.strftime("%Y-%m-%d %H:%M"), "count": len(events), "events": events}
    if failed:
        out["calendars_not_read"] = failed
    if note:
        out["note"] = note
    return out


def _events_all_calendars(t_min, t_max):
    """One batched HTTP round trip for every visible calendar; each event is tagged with its calendar."""
    svc, cals, rows, failed = _calendar(), _visible_calendars(), [], []

    def on_done(request_id, response, exception):
        name = cals[int(request_id)][1]
        if exception is not None:
            failed.append(name)
            return
        for ev in response.get("items", []):
            row = _event_row(ev)
            row["calendar"] = name
            if row["start_local"]:
                row["weekday"] = HEB_DAYS[datetime.strptime(row["start_local"][:10], "%Y-%m-%d").weekday()]
            rows.append(row)

    batch = svc.new_batch_http_request(callback=on_done)
    for i, (cid, _) in enumerate(cals):
        batch.add(svc.events().list(calendarId=cid, timeMin=t_min.isoformat(), timeMax=t_max.isoformat(),
                                    maxResults=25, singleEvents=True, orderBy="startTime"), request_id=str(i))
    batch.execute()
    return rows, failed


def search_calendar(query, days_ahead=365, days_back=30):
    """Keyword search across every visible calendar (Google's q= matches title, description, location).
    get_calendar only returns a date window, so "when is mom's birthday" had no fast answer (25/09)."""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "empty query"}
    tz = local_tz.home_tz()
    now = datetime.now(tz)
    t_min = now - timedelta(days=max(0, min(365, int(days_back or 0))))
    t_max = now + timedelta(days=max(1, min(730, int(days_ahead or 365))))
    svc, cals, rows = _calendar(), _visible_calendars(), []

    def on_done(request_id, response, exception):
        if exception is None:
            for ev in response.get("items", []):
                row = _event_row(ev)
                row["calendar"] = cals[int(request_id)][1]
                if row["start_local"]:
                    row["weekday"] = HEB_DAYS[datetime.strptime(row["start_local"][:10], "%Y-%m-%d").weekday()]
                rows.append(row)

    batch = svc.new_batch_http_request(callback=on_done)
    for i, (cid, _) in enumerate(cals):
        batch.add(svc.events().list(calendarId=cid, q=q, timeMin=t_min.isoformat(), timeMax=t_max.isoformat(),
                                    maxResults=10, singleEvents=True, orderBy="startTime"), request_id=str(i))
    batch.execute()
    rows.sort(key=lambda r: r["start_local"] or "")
    return {"ok": True, "query": q, "count": len(rows), "events": rows[:20]}


def _loose(text):
    """Hebrew spelling-insensitive form: no vav/yod (אמא == אימא). Only ever compared for EQUALITY -
    as a substring it is far too loose ("<contact>" -> "נ")."""
    return re.sub("[וי]", "", _plain(text or ""))


def _name_matches(query, candidate):
    q, c = _plain(query), _plain(candidate)
    if not q or not c:
        return False
    return (q == c or _loose(q) == _loose(c) or q in c.split()
            or any(_loose(q) == _loose(t) for t in c.split()) or (len(q) >= 3 and q in c))


def _birthdays_calendar_search(terms):
    """One batched request: every term against the birthdays calendar -> first date found, or None."""
    cal = next(((cid, n) for cid, n in _visible_calendars() if "b-days" in n.lower()), None)
    if cal is None:
        return None
    svc, now, found = _calendar(), datetime.now(local_tz.home_tz()), {}

    def on_done(request_id, response, exception):
        items = [] if exception is not None else response.get("items", [])
        # Exact title only: q= is a substring search, and "<contact>" found "<contact> <contact>", "<contact>" found "<contact> <contact>".
        items = [ev for ev in items if _loose(ev.get("summary", "")) == _loose(terms[int(request_id)])]
        if items:
            found[int(request_id)] = _event_row(items[0])["start_local"]

    batch = svc.new_batch_http_request(callback=on_done)
    for i, term in enumerate(terms):
        batch.add(svc.events().list(calendarId=cal[0], q=term, timeMin=now.isoformat(),
                                    timeMax=(now + timedelta(days=366)).isoformat(), maxResults=10,
                                    singleEvents=True, orderBy="startTime"), request_id=str(i))
    batch.execute()
    return found[min(found)] if found else None


def lookup_contact(name):
    """A person from the contacts DB (people + nicknames + notes), falling back to the phone book.
    If the card has no birthday, the birthdays calendar is searched for the name and nicknames."""
    if not _plain(name or ""):
        return {"ok": False, "error": "empty name"}
    conn = get_db()
    try:
        people = conn.execute(
            "SELECT p.id, p.name, p.birthday, p.phone, p.email, r.name FROM people p "
            "LEFT JOIN relationship_types r ON r.id = p.relationship_id").fetchall()
        nicks = {}
        for pid, nick in conn.execute("SELECT person_id, nickname FROM people_nicknames"):
            nicks.setdefault(pid, []).append(nick)
        hits = [p for p in people if any(_name_matches(name, n) for n in [p[1], *nicks.get(p[0], [])])]
        out = []
        for pid, pname, bday, phone, email, rel in hits[:3]:
            notes = [n for (n,) in conn.execute(
                "SELECT note FROM people_notes WHERE person_id=? ORDER BY id DESC LIMIT 15", (pid,))]
            out.append({"name": pname, "nicknames": nicks.get(pid, []), "relationship": rel, "birthday": bday,
                        "phone": phone, "email": email, "notes": notes})
        if not out:
            book = conn.execute("SELECT name, phone FROM phone_contacts").fetchall()
            matches = [{"name": n, "phone": ph} for n, ph in book if _name_matches(name, n)][:5]
            return {"ok": True, "found": bool(matches), "phone_book_only": True, "contacts": matches}
    finally:
        conn.close()
    for person in out:
        if not person["birthday"]:
            # The word Omer said first ("אמא" is how the calendar spells it; the card's nickname is "אימא").
            terms = list(dict.fromkeys([name.strip(), person["name"], *person["nicknames"]]))
            person["birthday_from_calendar"] = _birthdays_calendar_search(terms)
    return {"ok": True, "found": True, "contacts": out}


# ---------------------------------------------------------------- registry

FAST_TOOLS = {
    "list_reminders": list_reminders,
    "create_reminder": create_reminder,
    "reschedule_reminder": reschedule_reminder,
    "cancel_reminder": cancel_reminder,
    "list_tasks": list_tasks,
    "create_task": create_task,
    "complete_task": complete_task,
    "get_calendar": get_calendar,
    "search_calendar": search_calendar,
    "lookup_contact": lookup_contact,
    "get_time": lambda: {"ok": True, **now_info()},
}


def call_tool(name, args):
    fn = FAST_TOOLS.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown tool {name}"}
    try:
        return fn(**(args or {}))
    except TypeError as e:
        return {"ok": False, "error": f"bad arguments: {e}"}
    except Exception as e:  # never crash the live session over a tool
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
