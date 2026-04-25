#!/usr/bin/env python3
"""
Fast calendar update — update an existing event found by keyword search.
At least one of --title / --start / --end / --location must be provided.

Usage:
  python fast_cal_update.py --search "ארוחה" --title "ארוחת ערב עם אמא" \
    --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

  python fast_cal_update.py --search "פגישה" --start "2026-04-14 16:00" --duration 90 \
    --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

search: keyword matched against event summary (case-insensitive, partial)
Only searches the next 30 days. If multiple events match, updates the first (soonest).
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
from _gcal import get_calendar_service, parse_datetime_jerusalem
from datetime import datetime, timezone, timedelta

JERUSALEM = timezone(timedelta(hours=3))


def find_event(service, keyword, calendar_id="primary"):
    """Find first upcoming event matching keyword in summary."""
    now = datetime.now(JERUSALEM).isoformat()
    far = (datetime.now(JERUSALEM) + timedelta(days=365)).isoformat()

    result = service.events().list(
        calendarId=calendar_id,
        timeMin=now,
        timeMax=far,
        maxResults=50,
        singleEvents=True,
        orderBy="startTime",
        q=keyword
    ).execute()

    events = result.get("items", [])
    return events[0] if events else None


def main():
    p = base_args("Update a calendar event")
    p.add_argument("--search", required=True, help="Keyword to find the event")
    p.add_argument("--title", default=None, help="New title")
    p.add_argument("--start", default=None, help="New start: 'YYYY-MM-DD HH:MM' (Jerusalem)")
    p.add_argument("--end", default=None, help="New end: 'YYYY-MM-DD HH:MM' (Jerusalem)")
    p.add_argument("--duration", type=int, default=None, help="New duration in minutes (used if --start given but --end not)")
    p.add_argument("--location", default=None, help="New location")
    p.add_argument("--calendar", default="primary", help="Calendar ID (default: primary)")
    args = p.parse_args()

    if not any([args.title, args.start, args.end, args.location]):
        start_typing(args.chat_id, args.orig_id)
        fail(args, "חסר פרמטר — יש לספק לפחות אחד מ: --title, --start, --end, --location")
        return

    start_typing(args.chat_id, args.orig_id)
    try:
        service = get_calendar_service()

        event = find_event(service, args.search, args.calendar)
        if not event:
            fail(args, f'לא נמצא אירוע עם "{args.search}" בשנה הקרובה')
            return

        event_id = event["id"]
        old_title = event.get("summary", "?")

        # Build patch
        patch = {}
        if args.title:
            patch["summary"] = args.title

        if args.location:
            patch["location"] = args.location

        if args.start:
            patch["start"] = parse_datetime_jerusalem(args.start)
            if args.end:
                patch["end"] = parse_datetime_jerusalem(args.end)
            elif args.duration:
                from datetime import datetime as dt_cls
                start_dt = dt_cls.strptime(args.start, "%Y-%m-%d %H:%M")
                end_dt = start_dt + timedelta(minutes=args.duration)
                patch["end"] = parse_datetime_jerusalem(end_dt.strftime("%Y-%m-%d %H:%M"))
        elif args.end:
            patch["end"] = parse_datetime_jerusalem(args.end)

        service.events().patch(
            calendarId=args.calendar,
            eventId=event_id,
            body=patch
        ).execute()

        new_title = args.title or old_title
        lines = [
            f"{RLM}✅ אירוע עודכן:",
            f"{RLM}📌 {new_title}",
        ]
        if args.start:
            lines.append(f"{RLM}📅 {args.start}")
        if args.location:
            lines.append(f"{RLM}📍 {args.location}")

        action_log("fast_cal_update", "updated", f"search={args.search} event_id={event_id}")
        finish(args, "\n".join(lines))
    except FileNotFoundError as e:
        fail(args, f"יומן לא מחובר — {e}")
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")


if __name__ == "__main__":
    main()
