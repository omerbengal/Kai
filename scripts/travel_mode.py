#!/usr/bin/env python3
"""
travel_mode.py - turn travel mode ("מצב חו״ל") on/off and report its state.

  python travel_mode.py on --dest תאילנד --tz Asia/Bangkok [--dest_en Thailand] [--end 25/09] [--weather Bangkok]
  python travel_mode.py off
  python travel_mode.py status [--json]
  python travel_mode.py schedule            # next night shutdown/start + briefing, local and Israel time
  python travel_mode.py expire-check        # heartbeat: auto-return after the end date, Telegram message once

The only state is config/travel_mode.json. Everything time-related reads it via
scripts/local_tz.py. Existing reminders are stored in UTC and never move.
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import local_tz  # noqa: E402

RLM = "\u200F"
CHAT_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
BOT_TOKEN = os.environ["KAI_TELEGRAM_BOT_TOKEN"]
DB_PATH = "D:/Kai/kai.db"

KNOWN = {
    # tz -> (destination_en, default weather city)
    "Asia/Bangkok": ("Thailand", "Bangkok"),
    "Asia/Jerusalem": ("Israel", "Tel Aviv"),
}


def _write_config(cfg, path=None):
    path = path or local_tz.CONFIG_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def parse_end(value, today=None):
    """Accept YYYY-MM-DD, DD/MM/YYYY or DD/MM (next occurrence on/after today)."""
    if not value:
        return None
    value = value.strip()
    today = today or date.today()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        pass
    parts = value.replace(".", "/").split("/")
    if len(parts) == 3:
        y = int(parts[2]) + (2000 if len(parts[2]) == 2 else 0)
        return date(y, int(parts[1]), int(parts[0])).isoformat()
    if len(parts) == 2:
        d = date(today.year, int(parts[1]), int(parts[0]))
        if d < today:
            d = date(today.year + 1, d.month, d.day)
        return d.isoformat()
    raise ValueError(f"unrecognised date: {value}")


def fmt_dt(dt):
    return dt.strftime("%d/%m %H:%M")


def next_local_clock(hour, minute, now_utc, tz):
    """Next UTC instant at which the wall clock in tz reads hour:minute."""
    local_now = now_utc.astimezone(tz)
    d = local_now.date()
    if (hour, minute) <= (local_now.hour, local_now.minute):
        d = d + timedelta(days=1)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz).astimezone(timezone.utc)


def schedule_lines(now_utc=None, cfg=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    cfg = local_tz.load_config() if cfg is None else cfg
    tz = local_tz.home_tz(now_utc, cfg)
    il = local_tz.israel_tz()
    label = local_tz.tz_label(now_utc, cfg) or "ישראל"
    abroad = local_tz.is_active(now_utc, cfg)
    night_on = os.path.exists(r"D:\Kai\tmp\night_mode_enabled")
    out = []
    items = [("🌙 כיבוי לילה", 2, 0), ("🌅 הדלקה", 5, 0), ("☀️ תדרוך בוקר", 9, 0)]
    for name, h, m in items:
        if not night_on and h in (2, 5):
            continue
        u = next_local_clock(h, m, now_utc, tz)
        loc = f"{fmt_dt(u.astimezone(tz))} ({label})"
        if abroad:
            loc += f" = {u.astimezone(il):%H:%M} (ישראל)"
        out.append(f"{RLM}{name}: {loc}")
    if not night_on:
        out.append(f"{RLM}🌙 מצב לילה כבוי (אין tmp/night_mode_enabled)")
    return out


def status_text(now_utc=None, cfg=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    cfg = local_tz.load_config() if cfg is None else cfg
    tz = local_tz.home_tz(now_utc, cfg)
    if local_tz.is_active(now_utc, cfg):
        end = cfg.get("end")
        end_txt = f"עד {date.fromisoformat(end):%d/%m} (כולל)" if end else "ללא תאריך סיום"
        head = [
            f"{RLM}🌍 מצב חו״ל פעיל: {cfg.get('destination_he')} ({cfg['tz']}, {local_tz.utc_offset_str(tz, now_utc)})",
            f"{RLM}📅 {end_txt}",
            f"{RLM}🕐 עכשיו: {now_utc.astimezone(tz):%H:%M} ({cfg.get('destination_he')}) | {now_utc.astimezone(local_tz.israel_tz()):%H:%M} (ישראל)",
        ]
    else:
        head = [f"{RLM}🇮🇱 שעון ישראל ({local_tz.utc_offset_str(tz, now_utc)}) - מצב חו״ל כבוי"]
    return "\n".join(head + [""] + schedule_lines(now_utc, cfg))


def cmd_on(args):
    try:
        local_tz._zone(args.tz)
    except Exception:
        print(f"ERROR: unknown timezone {args.tz}", file=sys.stderr)
        return 2
    known = KNOWN.get(args.tz, (None, None))
    cfg = {
        "active": True,
        "destination_he": args.dest,
        "destination_en": args.dest_en or known[0] or args.tz.split("/")[-1].replace("_", " "),
        "tz": args.tz,
        "weather_city": args.weather or known[1] or args.tz.split("/")[-1].replace("_", " "),
        "start": datetime.now(local_tz._zone(args.tz)).date().isoformat(),
        "end": parse_end(args.end) if args.end else None,
    }
    old = local_tz.load_config()
    if old.get("active") and old.get("tz") == cfg["tz"] and old.get("start"):
        cfg["start"] = old["start"]  # re-running "on" to set an end date keeps the trip start
    _write_config(cfg)
    _log("travel_mode_on", json.dumps(cfg, ensure_ascii=False))
    print(status_text())
    return 0


def cmd_off(args):
    cfg = local_tz.load_config()
    cfg["active"] = False
    cfg["ended_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    _write_config(cfg)
    _log("travel_mode_off", cfg.get("destination_en", ""))
    print(status_text())
    return 0


def _log(action, details):
    try:
        import sqlite3
        c = sqlite3.connect(DB_PATH)
        c.execute("INSERT INTO action_log (agent, action, details) VALUES (?, ?, ?)", ("travel_mode", action, details))
        c.commit()
        c.close()
    except Exception:
        pass


def expire_check(now_utc=None, send=True, path=None):
    """If the end date passed: deactivate and (once) tell Omer. Returns True when it expired now."""
    cfg = local_tz.load_config(path)
    if not local_tz.is_expired(cfg, now_utc):
        return False
    cfg["active"] = False
    cfg["ended_at"] = (now_utc or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")
    cfg["ended_reason"] = "end_date"
    _write_config(cfg, path)
    if send:
        _log("travel_mode_expired", cfg.get("destination_en", ""))
        text = (f"{RLM}🇮🇱 מצב חו״ל ({cfg.get('destination_he')}) הסתיים לפי תאריך הסיום - חזרתי לשעון ישראל ✅\n\n"
                + "\n".join(schedule_lines(now_utc)))
        try:
            data = json.dumps({"chat_id": CHAT_ID, "text": text}, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data,
                                         {"Content-Type": "application/json; charset=utf-8"})
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass
    return True


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Travel mode control")
    sub = p.add_subparsers(dest="cmd", required=True)
    on = sub.add_parser("on")
    on.add_argument("--dest", required=True, help="Hebrew destination name, e.g. תאילנד")
    on.add_argument("--tz", required=True, help="IANA timezone, e.g. Asia/Bangkok")
    on.add_argument("--dest_en", default=None)
    on.add_argument("--end", default=None, help="Last day abroad (inclusive): YYYY-MM-DD or DD/MM")
    on.add_argument("--weather", default=None, help="Weather city, e.g. Bangkok")
    sub.add_parser("off")
    st = sub.add_parser("status")
    st.add_argument("--json", action="store_true")
    sub.add_parser("schedule")
    sub.add_parser("expire-check")
    args = p.parse_args(argv)

    if args.cmd == "on":
        return cmd_on(args)
    if args.cmd == "off":
        return cmd_off(args)
    if args.cmd == "status":
        if args.json:
            cfg = local_tz.load_config()
            print(json.dumps({"config": cfg, "active": local_tz.is_active(cfg=cfg),
                              "home_tz": local_tz.home_tz_name(cfg=cfg),
                              "now_local": local_tz.now_local(cfg=cfg).isoformat()}, ensure_ascii=False, indent=2))
        else:
            print(status_text())
        return 0
    if args.cmd == "schedule":
        print("\n".join(schedule_lines()))
        return 0
    if args.cmd == "expire-check":
        print("expired" if expire_check() else "ok")
        return 0


if __name__ == "__main__":
    sys.exit(main())
