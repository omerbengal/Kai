"""Travel mode (2026-09-13): local_tz, night-mode slots, briefing gate, reminder parsing.

Every test injects the clock and the config - nothing here reads the live
config, sends a message, or touches a scheduled task or process.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, "D:/Kai/scripts")
import local_tz  # noqa: E402
import night_mode  # noqa: E402
import run_morning_briefing as rmb  # noqa: E402
import travel_mode  # noqa: E402

UTC = timezone.utc
BKK = {"active": True, "destination_he": "תאילנד", "destination_en": "Thailand",
       "tz": "Asia/Bangkok", "weather_city": "Bangkok", "start": "2026-09-13", "end": None}
OFF = {}


def utc(*a):
    return datetime(*a, tzinfo=UTC)


# ---------------------------------------------------------------- local_tz

def test_israel_default_when_no_config():
    now = utc(2026, 9, 13, 5, 0)
    assert local_tz.home_tz_name(now, OFF) == "Asia/Jerusalem"
    assert local_tz.now_local(now, OFF).hour == 8
    assert local_tz.tz_label(now, OFF) == ""
    assert local_tz.fmt_local("2026-09-13 09:00:00", now_utc=now, cfg=OFF) == "12:00"


def test_broken_config_file_falls_back_to_israel(tmp_path):
    p = tmp_path / "travel_mode.json"
    p.write_text("{not json", encoding="utf-8")
    assert local_tz.load_config(str(p)) == {}
    bad_tz = dict(BKK, tz="Mars/Olympus")
    assert local_tz.home_tz_name(utc(2026, 9, 13), bad_tz) == "Asia/Jerusalem"


def test_active_bangkok():
    now = utc(2026, 9, 13, 5, 35)
    assert local_tz.is_active(now, BKK)
    assert local_tz.now_local(now, BKK).strftime("%H:%M") == "12:35"
    assert local_tz.utc_offset_str(local_tz.home_tz(now, BKK), now) == "UTC+7"
    assert local_tz.fmt_local("2026-09-14 05:00:00", "%d/%m %H:%M", now_utc=now, cfg=BKK) == "14/09 12:00 (תאילנד)"
    assert local_tz.sqlite_offset_modifier(now, BKK) == "+420 minutes"


def test_end_date_is_inclusive_then_expires():
    cfg = dict(BKK, end="2026-09-25")
    assert local_tz.is_active(utc(2026, 9, 25, 16, 59), cfg)      # 23:59 Bangkok on the end date
    assert not local_tz.is_active(utc(2026, 9, 25, 17, 0), cfg)   # 00:00 Bangkok the day after
    assert local_tz.home_tz_name(utc(2026, 9, 26), cfg) == "Asia/Jerusalem"


def test_expire_check_deactivates_once(tmp_path):
    p = str(tmp_path / "travel_mode.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(dict(BKK, end="2026-09-25"), f)
    assert travel_mode.expire_check(utc(2026, 9, 20), send=False, path=p) is False
    assert travel_mode.expire_check(utc(2026, 9, 26), send=False, path=p) is True
    assert local_tz.load_config(p)["active"] is False
    assert travel_mode.expire_check(utc(2026, 9, 27), send=False, path=p) is False


def test_israel_dst_boundary_october_2026():
    # Israel leaves DST on Sunday 2026-10-25 at 02:00 -> UTC+2
    before = utc(2026, 10, 24, 12, 0)
    after = utc(2026, 10, 26, 12, 0)
    assert local_tz.now_local(before, OFF).utcoffset() == timedelta(hours=3)
    assert local_tz.now_local(after, OFF).utcoffset() == timedelta(hours=2)
    assert local_tz.local_str_to_utc_str("2026-10-24 12:00", cfg=OFF) == "2026-10-24 09:00:00"
    assert local_tz.local_str_to_utc_str("2026-10-26 12:00", cfg=OFF) == "2026-10-26 10:00:00"


def test_parse_utc_accepts_db_and_iso_formats():
    for v in ("2026-09-14 05:00:00", "2026-09-14T05:00:00Z", "2026-09-14T05:00:00+00:00", "2026-09-14T08:00:00+03:00"):
        assert local_tz.parse_utc(v) == utc(2026, 9, 14, 5, 0)


# ---------------------------------------------------------------- reminders

def test_reminder_tomorrow_at_noon_abroad_vs_forced_israel():
    now = utc(2026, 9, 13, 5, 35)
    # "מחר ב-12:00" while in Thailand = 12:00 Bangkok = 05:00 UTC
    assert local_tz.local_str_to_utc_str("2026-09-14 12:00", now_utc=now, cfg=BKK) == "2026-09-14 05:00:00"
    # "מחר ב-12:00 שעון ישראל" = 09:00 UTC, displayed abroad with an Israel label
    forced = local_tz.local_str_to_utc_str("2026-09-14 12:00", force_israel=True, now_utc=now, cfg=BKK)
    assert forced == "2026-09-14 09:00:00"
    assert local_tz.fmt_local(forced, now_utc=now, cfg=BKK, force_israel=True) == "12:00 (ישראל)"
    assert local_tz.fmt_local(forced, now_utc=now, cfg=BKK) == "16:00 (תאילנד)"


def test_existing_reminder_keeps_absolute_moment_after_return():
    stored = local_tz.local_str_to_utc_str("<REDACTED_SECRET> 12:00", now_utc=utc(2026, 9, 13), cfg=BKK)
    # after "חזרתי" the same UTC moment is simply shown in Israel time
    assert local_tz.fmt_local(stored, now_utc=utc(2026, 9, 26), cfg=OFF) == "08:00"


# ---------------------------------------------------------------- night mode

@pytest.mark.parametrize("cfg,now,action,expected", [
    (OFF, utc(2026, 9, 13, 23, 0), "Shutdown", True),    # 02:00 Israel
    (OFF, utc(2026, 9, 13, 23, 30), "Shutdown", False),  # 02:30 Israel - slot over
    (OFF, utc(2026, 9, 13, 19, 0), "Shutdown", False),   # 22:00 Israel
    (BKK, utc(2026, 9, 13, 19, 0), "Shutdown", True),    # 02:00 Bangkok
    (BKK, utc(2026, 9, 13, 19, 30), "Shutdown", False),  # 02:30 Bangkok
    (BKK, utc(2026, 9, 13, 23, 0), "Shutdown", False),   # 06:00 Bangkok
    (BKK, utc(2026, 9, 13, 22, 0), "Start", True),       # 05:00 Bangkok
    (BKK, utc(2026, 9, 13, 22, 5), "Start", True),       # task a few minutes late
    (OFF, utc(2026, 9, 14, 2, 0), "Start", True),        # 05:00 Israel
    (BKK, utc(2026, 9, 14, 2, 0), "Start", False),       # 09:00 Bangkok
])
def test_night_slot_decision(cfg, now, action, expected):
    run, _ = night_mode.slot_decision(action, now, state={}, cfg=cfg)
    assert run is expected


def test_night_slot_runs_once_per_night(tmp_path):
    state = str(tmp_path / "state.json")
    first = utc(2026, 9, 13, 19, 0)
    assert night_mode.claim_slot("Shutdown", first, state_path=state, cfg=BKK) == (True, "run")
    assert night_mode.claim_slot("Shutdown", first + timedelta(minutes=10), state_path=state, cfg=BKK) == (False, "already_ran")
    # next night acts again
    assert night_mode.claim_slot("Shutdown", first + timedelta(days=1), state_path=state, cfg=BKK)[0] is True


def test_night_slot_dry_run_records_nothing(tmp_path):
    state = str(tmp_path / "state.json")
    assert night_mode.claim_slot("Shutdown", utc(2026, 9, 13, 19, 0), dry_run=True, state_path=state, cfg=BKK)[0]
    assert not os.path.exists(state)


def test_switching_tz_mid_trip_does_not_double_shutdown(tmp_path):
    state = str(tmp_path / "state.json")
    # shut down at 02:00 Bangkok (19:00 UTC), then Omer "returns" and Israel 02:00 comes 4h later
    assert night_mode.claim_slot("Shutdown", utc(2026, 9, 13, 19, 0), state_path=state, cfg=BKK)[0]
    assert night_mode.claim_slot("Shutdown", utc(2026, 9, 13, 23, 0), state_path=state, cfg=OFF) == (False, "already_ran")


def test_watchdog_quiet_window_is_local():
    assert night_mode.in_quiet_window(utc(2026, 9, 13, 19, 0), cfg=BKK)       # 02:00 Bangkok
    assert not night_mode.in_quiet_window(utc(2026, 9, 13, 23, 0), cfg=BKK)   # 06:00 Bangkok
    assert night_mode.in_quiet_window(utc(2026, 9, 13, 23, 0), cfg=OFF)       # 02:00 Israel
    assert not night_mode.in_quiet_window(utc(2026, 9, 13, 22, 11), cfg=BKK)  # 05:11 Bangkok


# ---------------------------------------------------------------- briefing

@pytest.mark.parametrize("hour,sent,expected", [(9, False, True), (9, True, False), (8, False, False), (10, False, False), (13, False, False)])
def test_briefing_gate(hour, sent, expected):
    now_local = datetime(2026, 9, 14, hour, 0)
    assert rmb.briefing_gate(now_local, sent)[0] is expected


def _log_db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE action_log (id INTEGER PRIMARY KEY, agent TEXT, action TEXT, details TEXT, created_at TEXT)")
    for r in rows:
        conn.execute("INSERT INTO action_log (agent, action, details, created_at) VALUES ('morning_briefing','briefing_sent','x',?)", (r,))
    return conn


def test_briefing_dedupe_uses_local_date():
    # Israel briefing sent 2026-09-13 06:00 UTC (= 13:00 Bangkok, 13/09).
    conn = _log_db(["2026-09-13 06:00:07"])
    # 09:00 Bangkok on 14/09 = 02:00 UTC 14/09 -> new local day, not a duplicate
    assert rmb.already_sent_on(conn, "2026-09-14", "+420 minutes") is False
    # ...but a UTC-date comparison would have been fooled by 22:30 UTC sends; local 13/09 is taken
    assert rmb.already_sent_on(conn, "2026-09-13", "+420 minutes") is True
    # a send at 02:00 UTC 14/09 (09:00 Bangkok) blocks the rest of Bangkok 14/09
    conn2 = _log_db(["2026-09-14 02:00:05"])
    assert rmb.already_sent_on(conn2, "2026-09-14", "+420 minutes") is True
    assert rmb.already_sent_on(conn2, "2026-09-15", "+420 minutes") is False


def test_next_schedule_bangkok_tonight():
    now = utc(2026, 9, 13, 6, 30)  # 13:30 Bangkok
    tz = local_tz.home_tz(now, BKK)
    assert travel_mode.next_local_clock(2, 0, now, tz) == utc(2026, 9, 13, 19, 0)
    assert travel_mode.next_local_clock(5, 0, now, tz) == utc(2026, 9, 13, 22, 0)
    assert travel_mode.next_local_clock(9, 0, now, tz) == utc(2026, 9, 14, 2, 0)


def test_parse_end_date_forms():
    from datetime import date
    today = date(2026, 9, 13)
    assert travel_mode.parse_end("25/09", today) == "2026-09-25"
    assert travel_mode.parse_end("2026-09-25", today) == "2026-09-25"
    assert travel_mode.parse_end("05/01", today) == "2027-01-05"
