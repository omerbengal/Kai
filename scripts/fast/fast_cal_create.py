#!/usr/bin/env python3
"""
Fast calendar create — add a new event.
Usage:
  python fast_cal_create.py --title "פגישה" --start "2026-04-13 15:00" --duration 60 \
    --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

  # With end time instead of duration:
  python fast_cal_create.py --title "פגישה" --start "2026-04-13 15:00" --end "2026-04-13 16:30" \
    --location "תל אביב" --attendees "a@b.com,c@d.com" --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

start/end: Jerusalem local time "YYYY-MM-DD HH:MM"
duration: minutes (default 60), used only if --end is not provided
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
from _gcal import get_calendar_service, parse_datetime_jerusalem, TIMEZONE
from datetime import datetime, timedelta


def add_minutes(dt_str, minutes):
    """Add minutes to a 'YYYY-MM-DD HH:MM' string, return same format."""
    dt = datetime.strptime(dt_str.strip(), "%Y-%m-%d %H:%M")
    dt2 = dt + timedelta(minutes=minutes)
    return dt2.strftime("%Y-%m-%d %H:%M")


def main():
    p = base_args("Create a calendar event")
    p.add_argument("--title", required=True, help="Event title")
    p.add_argument("--start", required=True, help="Start time: 'YYYY-MM-DD HH:MM' (Jerusalem)")
    p.add_argument("--end", default=None, help="End time: 'YYYY-MM-DD HH:MM' (Jerusalem)")
    p.add_argument("--duration", type=int, default=60, help="Duration in minutes (used if --end not given, default: 60)")
    p.add_argument("--calendar", default="primary", help="Calendar ID (default: primary)")
    p.add_argument("--location", default=None, help="Location (optional)")
    p.add_argument("--attendees", default=None, help="Comma-separated email addresses (optional)")
    args = p.parse_args()

    start_typing(args.chat_id, args.orig_id)
    try:
        service = get_calendar_service()

        end_str = args.end if args.end else add_minutes(args.start, args.duration)

        event_body = {
            "summary": args.title,
            "start": parse_datetime_jerusalem(args.start),
            "end": parse_datetime_jerusalem(end_str),
        }

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
        start_disp = args.start
        end_disp = end_str
        lines = [
            f"{RLM}✅ אירוע נוצר:",
            f"{RLM}📌 {args.title}",
            f"{RLM}📅 {start_disp} — {end_disp.split(' ')[1] if ' ' in end_disp else end_disp}",
        ]
        if args.location:
            lines.append(f"{RLM}📍 {args.location}")
        if args.attendees:
            lines.append(f"{RLM}👥 {args.attendees}")

        action_log("fast_cal_create", "created", f"title={args.title} start={args.start}")
        finish(args, "\n".join(lines))
    except FileNotFoundError as e:
        fail(args, f"יומן לא מחובר — {e}")
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")


if __name__ == "__main__":
    main()
