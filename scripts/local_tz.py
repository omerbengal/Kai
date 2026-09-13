#!/usr/bin/env python3
"""
local_tz.py - the single source of truth for "what time is it where Omer is".

At home that is Israel (Asia/Jerusalem, DST handled by zoneinfo). While travel
mode is on (config/travel_mode.json, managed by scripts/travel_mode.py) it is
the destination timezone, until the end date passes.

Everything that turns a clock time into UTC or a UTC value into a displayed
time must go through here. Never hard-code UTC+3.

Fail-safe by design: any problem reading the config means Israel time. If
zoneinfo cannot even load Asia/Jerusalem (tzdata missing), it falls back to a
fixed UTC+3, which is what every script did before this module existed.

Stdlib + tzdata only.
"""
import json
import os
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

CONFIG_PATH = os.environ.get("KAI_TRAVEL_CONFIG", "D:/Kai/config/travel_mode.json")
ISRAEL_TZ_NAME = "Asia/Jerusalem"
ISRAEL_LABEL_HE = "ישראל"
_LEGACY_ISRAEL = timezone(timedelta(hours=3), "UTC+3")


def _zone(name):
    if ZoneInfo is None:
        raise RuntimeError("zoneinfo unavailable")
    return ZoneInfo(name)


def israel_tz():
    try:
        return _zone(ISRAEL_TZ_NAME)
    except Exception:
        return _LEGACY_ISRAEL


def load_config(path=None):
    """Return the travel config dict. Never raises; a broken file reads as inactive."""
    try:
        with open(path or CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _utc_now(now_utc=None):
    if now_utc is None:
        return datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        return now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(timezone.utc)


def _parse_end(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def is_expired(cfg, now_utc=None):
    """True when the config is marked active but its end date has passed (destination-local)."""
    if not cfg.get("active"):
        return False
    end = _parse_end(cfg.get("end"))
    if end is None:
        return False
    try:
        dest = _zone(cfg["tz"])
    except Exception:
        return False
    return _utc_now(now_utc).astimezone(dest).date() > end


def is_active(now_utc=None, cfg=None):
    """Travel mode is on, its timezone loads, and the (inclusive) end date has not passed."""
    cfg = load_config() if cfg is None else cfg
    try:
        if not cfg.get("active") or not cfg.get("tz"):
            return False
        _zone(cfg["tz"])
        return not is_expired(cfg, now_utc)
    except Exception:
        return False


def home_tz(now_utc=None, cfg=None, force_israel=False):
    """The timezone Omer lives in right now."""
    if force_israel:
        return israel_tz()
    cfg = load_config() if cfg is None else cfg
    if is_active(now_utc, cfg):
        try:
            return _zone(cfg["tz"])
        except Exception:
            pass
    return israel_tz()


def home_tz_name(now_utc=None, cfg=None):
    cfg = load_config() if cfg is None else cfg
    return cfg["tz"] if is_active(now_utc, cfg) else ISRAEL_TZ_NAME


def now_local(now_utc=None, cfg=None, force_israel=False):
    return _utc_now(now_utc).astimezone(home_tz(now_utc, cfg, force_israel))


def tz_label(now_utc=None, cfg=None):
    """Hebrew destination name while abroad ("תאילנד"), empty string at home."""
    cfg = load_config() if cfg is None else cfg
    if is_active(now_utc, cfg):
        return cfg.get("destination_he") or cfg.get("destination_en") or cfg.get("tz", "")
    return ""


def utc_offset_str(tz=None, now_utc=None):
    """'UTC+7', 'UTC+3', 'UTC+5:30'."""
    tz = tz or home_tz(now_utc)
    off = _utc_now(now_utc).astimezone(tz).utcoffset() or timedelta(0)
    mins = int(off.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    h, m = divmod(abs(mins), 60)
    return f"UTC{sign}{h}" + (f":{m:02d}" if m else "")


def sqlite_offset_modifier(now_utc=None, cfg=None):
    """SQLite datetime modifier for the current local offset, e.g. '+420 minutes'."""
    off = now_local(now_utc, cfg).utcoffset() or timedelta(0)
    return "%+d minutes" % int(off.total_seconds() // 60)


def parse_utc(value):
    """Parse a DB/API UTC value ('YYYY-MM-DD HH:MM:SS', ISO with T/Z/offset) into an aware UTC datetime."""
    if isinstance(value, datetime):
        return _utc_now(value)
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.strptime(s.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
    return _utc_now(dt)


def to_utc(local_naive, force_israel=False, now_utc=None, cfg=None):
    """Interpret a naive wall-clock datetime in the home timezone (or Israel) and return aware UTC."""
    if local_naive.tzinfo is not None:
        return local_naive.astimezone(timezone.utc)
    tz = home_tz(now_utc, cfg, force_israel)
    return local_naive.replace(tzinfo=tz).astimezone(timezone.utc)


def parse_local(value, force_israel=False, now_utc=None, cfg=None):
    """'YYYY-MM-DD HH:MM[:SS]' (or with T) in home time -> aware UTC datetime."""
    s = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            naive = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"expected 'YYYY-MM-DD HH:MM', got {value!r}")
    return to_utc(naive, force_israel, now_utc, cfg)


def utc_db_str(dt):
    """Canonical DB storage format: 'YYYY-MM-DD HH:MM:SS' in UTC, space separator."""
    return _utc_now(dt).strftime("%Y-%m-%d %H:%M:%S")


def local_str_to_utc_str(value, force_israel=False, now_utc=None, cfg=None):
    return utc_db_str(parse_local(value, force_israel, now_utc, cfg))


def fmt_local(utc_value, fmt="%H:%M", label=True, now_utc=None, cfg=None, force_israel=False):
    """Format a UTC value in home time. Abroad appends ' (תאילנד)'; forced Israel abroad appends ' (ישראל)'."""
    cfg = load_config() if cfg is None else cfg
    dt = parse_utc(utc_value)
    abroad = is_active(now_utc, cfg)
    tz = israel_tz() if force_israel else home_tz(now_utc, cfg)
    text = dt.astimezone(tz).strftime(fmt)
    if label and abroad:
        text += f" ({ISRAEL_LABEL_HE})" if force_israel else f" ({tz_label(now_utc, cfg)})"
    return text


def session_context_line(now_utc=None, cfg=None):
    """One block injected into every new Claude session (SessionStart hook)."""
    cfg = load_config() if cfg is None else cfg
    now = _utc_now(now_utc)
    if is_active(now, cfg):
        tz = home_tz(now, cfg)
        end = cfg.get("end") or "לא נקבע"
        return (
            "=== TIMEZONE ===\n"
            f"Travel mode ACTIVE: {tz_label(now, cfg)} ({cfg['tz']}, {utc_offset_str(tz, now)}), end date: {end}.\n"
            f"Local time now: {now.astimezone(tz):%Y-%m-%d %H:%M} ({tz_label(now, cfg)}) | "
            f"Israel: {now.astimezone(israel_tz()):%H:%M}.\n"
            "Every time Omer says is destination-local unless he says 'שעון ישראל'. Label displayed times "
            f"'HH:MM ({tz_label(now, cfg)})'. Convert with scripts/local_tz.py - never assume UTC+3.\n"
            "================"
        )
    tz = israel_tz()
    return (
        "=== TIMEZONE ===\n"
        f"Home timezone: Israel ({ISRAEL_TZ_NAME}, {utc_offset_str(tz, now)}). Local time now: "
        f"{now.astimezone(tz):%Y-%m-%d %H:%M}. Travel mode off. Convert with scripts/local_tz.py - never assume UTC+3.\n"
        "================"
    )


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(session_context_line())
