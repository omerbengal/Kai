#!/usr/bin/env python3
"""
Fast calendar create — add a new event.
Usage:
  python fast_cal_create.py --title "פגישה" --start "2026-04-13 15:00" --duration 60 \
    --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

  # With end time instead of duration:
  python fast_cal_create.py --title "פגישה" --start "2026-04-13 15:00" --end "2026-04-13 16:30" \
    --location "תל אביב" --attendees "a@b.com,c@d.com" --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

  # All-day event (single day):
  python fast_cal_create.py --title "יום חופש" --all_day "2026-05-01" \
    --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

  # All-day event (multi-day, end is INCLUSIVE — script converts to exclusive for Google API):
  python fast_cal_create.py --title "חופשה" --all_day "2026-05-01" --all_day_end "2026-05-03" \
    --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

start/end: Jerusalem local time "YYYY-MM-DD HH:MM"
duration: minutes (default 60), used only if --end is not provided
all_day / all_day_end: "YYYY-MM-DD" — when --all_day is given, --start is ignored.
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
import local_tz  # home timezone (travel-mode aware) - scripts/local_tz.py
from _gcal import get_calendar_service, parse_datetime_jerusalem, TIMEZONE
from datetime import datetime, timedelta


def add_minutes(dt_str, minutes):
    """Add minutes to a 'YYYY-MM-DD HH:MM' string, return same format."""
    dt = datetime.strptime(dt_str.strip(), "%Y-%m-%d %H:%M")
    dt2 = dt + timedelta(minutes=minutes)
    return dt2.strftime("%Y-%m-%d %H:%M")


def add_days(date_str, days):
    """Add days to a 'YYYY-MM-DD' string, return same format."""
    d = datetime.strptime(date_str.strip(), "%Y-%m-%d")
    return (d + timedelta(days=days)).strftime("%Y-%m-%d")


def main():
    p = base_args("Create a calendar event")
    p.add_argument("--title", required=True, help="Event title")
    p.add_argument("--start", default=None, help="Start time: 'YYYY-MM-DD HH:MM' (Jerusalem). Required unless --all_day is given.")
    p.add_argument("--end", default=None, help="End time: 'YYYY-MM-DD HH:MM' (Jerusalem)")
    p.add_argument("--duration", type=int, default=60, help="Duration in minutes (used if --end not given, default: 60)")
    p.add_argument("--all_day", default=None, help="All-day event start date 'YYYY-MM-DD' (Jerusalem). When given, --start is ignored.")
    p.add_argument("--all_day_end", default=None, help="All-day event end date 'YYYY-MM-DD' INCLUSIVE (default: same as --all_day, single day).")
    p.add_argument("--calendar", default="primary", help="Calendar ID (default: primary)")
    p.add_argument("--location", default=None, help="Location (optional)")
    p.add_argument("--attendees", default=None, help="Comma-separated email addresses (optional)")
    args = p.parse_args()

    if not args.all_day and not args.start:
        p.error("either --start or --all_day is required")

    start_typing(args.chat_id, args.orig_id)
    try:
        service = get_calendar_service()

        if args.all_day:
            # All-day event. Google Calendar end.date is EXCLUSIVE,
            # so we add 1 day to whatever inclusive end the user gave.
            inclusive_end = args.all_day_end if args.all_day_end else args.all_day
            exclusive_end = add_days(inclusive_end, 1)
            event_body = {
                "summary": args.title,
                "start": parse_datetime_jerusalem(args.all_day),       # → {"date": "..."}
                "end":   parse_datetime_jerusalem(exclusive_end),      # → {"date": "..."}
            }
            start_disp = args.all_day
            end_disp = inclusive_end if inclusive_end != args.all_day else None
        else:
            end_str = args.end if args.end else add_minutes(args.start, args.duration)
            event_body = {
                "summary": args.title,
                "start": parse_datetime_jerusalem(args.start),
                "end":   parse_datetime_jerusalem(end_str),
            }
            start_disp = args.start
            end_disp = end_str

        if args.location:
            event_body["location"] = args.location

        if args.attendees:
            emails = [e.strip() for e in args.attendees.split(",") if e.strip()]
            event_body["attendees"] = [{"email": e} for e in emails]

        created = service.events().insert(
            calendarId=args.calendar,
            body=event_body
        ).execute()

        # Format confirmation
        if args.all_day:
            if end_disp:
                date_line = f"{RLM}📅 {start_disp} — {end_disp} (יום שלם)"
            else:
                date_line = f"{RLM}📅 {start_disp} (יום שלם)"
        else:
            date_line = f"{RLM}📅 {start_disp} — {end_disp.split(' ')[1] if ' ' in end_disp else end_disp}"
            if local_tz.tz_label():
                date_line += f" ({local_tz.tz_label()})"

        lines = [
            f"{RLM}✅ אירוע נוצר:",
            f"{RLM}📌 {args.title}",
            date_line,
        ]
        if args.location:
            lines.append(f"{RLM}📍 {args.location}")
        if args.attendees:
            lines.append(f"{RLM}👥 {args.attendees}")

        log_start = args.all_day if args.all_day else args.start
        action_log("fast_cal_create", "created", f"title={args.title} start={log_start}")
        finish(args, "\n".join(lines))
    except FileNotFoundError as e:
        fail(args, f"יומן לא מחובר — {e}")
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")


if __name__ == "__main__":
    main()
