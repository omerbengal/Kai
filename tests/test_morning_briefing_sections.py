"""Morning briefing: declined calendar events are hidden; tomorrow's reminders get their own section."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_morning_briefing as rmb  # noqa: E402


def _ev(summary, attendees=None):
    ev = {"summary": summary, "start": {"dateTime": "2026-10-31T10:00:00+02:00"}}
    if attendees is not None:
        ev["attendees"] = attendees
    return ev


def test_declined_by_omer_is_dropped():
    ev = _ev("party", [{"email": "me", "self": True, "responseStatus": "declined"},
                       {"email": "host", "organizer": True, "responseStatus": "accepted"}])
    assert rmb._drop_declined([ev]) == []


def test_other_attendee_declining_keeps_event():
    ev = _ev("meeting", [{"email": "me", "self": True, "responseStatus": "accepted"},
                         {"email": "other", "responseStatus": "declined"}])
    assert rmb._drop_declined([ev]) == [ev]


def test_tentative_unanswered_and_own_events_stay():
    tentative = _ev("a", [{"email": "me", "self": True, "responseStatus": "tentative"}])
    unanswered = _ev("b", [{"email": "me", "self": True, "responseStatus": "needsAction"}])
    own = _ev("c")
    assert rmb._drop_declined([tentative, unanswered, own]) == [tentative, unanswered, own]


def test_event_started_on_earlier_day_shows_as_ongoing():
    start = (datetime.now(rmb.JLM) - timedelta(days=3)).replace(hour=20, minute=40)
    ev = {"summary": "long", "start": {"dateTime": start.isoformat()}}
    assert rmb._format_calendar([ev]) == ["• נמשך - long"]


def test_all_day_event_started_on_earlier_day_shows_as_ongoing():
    d = (datetime.now(rmb.JLM).date() - timedelta(days=3)).isoformat()
    assert rmb._format_calendar([{"summary": "trip", "start": {"date": d}}]) == ["• נמשך - trip"]


def test_events_starting_today_keep_their_time():
    now = datetime.now(rmb.JLM)
    timed = {"summary": "a", "start": {"dateTime": now.replace(hour=20, minute=40).isoformat()}}
    all_day = {"summary": "b", "start": {"date": now.date().isoformat()}}
    assert rmb._format_calendar([timed, all_day]) == ["• 20:40 - a", "• כל היום - b"]


def _compose_reminders(*rems):
    return rmb._compose({"header": "", "reminders": list(rems)}).split("\n")


def _section_of(lines, needle):
    idx = next(i for i, l in enumerate(lines) if needle in l)
    while idx >= 0 and lines[idx].strip("‏ ").startswith("•"):
        idx -= 1
    return next(l for l in reversed(lines[:idx + 1]) if l.strip("‏ "))


def test_tomorrow_reminders_are_in_their_own_section():
    today = datetime.now(rmb.JLM).date()
    lines = _compose_reminders(
        {"message": "today-item", "time": "10:15", "date": today.isoformat()},
        {"message": "tomorrow-item", "time": "09:00", "date": (today + timedelta(days=1)).isoformat()},
    )
    assert "תזכורות היום" in _section_of(lines, "today-item")
    assert "תזכורות למחר" in _section_of(lines, "tomorrow-item")
    assert not any("(מחר)" in l for l in lines)


def test_tomorrow_only_has_no_today_heading():
    tomorrow = datetime.now(rmb.JLM).date() + timedelta(days=1)
    lines = _compose_reminders({"message": "x", "time": "09:00", "date": tomorrow.isoformat()})
    assert not any("תזכורות היום" in l for l in lines)
    assert any("תזכורות למחר" in l for l in lines)


def test_overdue_reminder_stays_in_today_and_is_not_called_tomorrow():
    yesterday = datetime.now(rmb.JLM).date() - timedelta(days=1)
    lines = _compose_reminders({"message": "old-item", "time": "09:00", "date": yesterday.isoformat()})
    line = next(l for l in lines if "old-item" in l)
    assert "(באיחור)" in line
    assert "תזכורות היום" in _section_of(lines, "old-item")
    assert not any("תזכורות למחר" in l for l in lines)
