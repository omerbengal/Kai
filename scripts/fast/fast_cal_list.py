#!/usr/bin/env python3
"""
Fast calendar list — show upcoming events.
Usage: python fast_cal_list.py --days 7 --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
import local_tz  # home timezone (travel-mode aware) - scripts/local_tz.py
from _gcal import get_calendar_service, format_event_for_display
from datetime import datetime, timezone, timedelta

JERUSALEM = local_tz.home_tz()  # was fixed UTC+3; travel-mode aware
MAX_EVENTS = 20


def main():
    p = base_args("List upcoming calendar events")
    p.add_argument("--days", type=int, default=7, help="How many days ahead to look (default: 7)")
    p.add_argument("--calendar", default="primary", help="Calendar ID (default: primary)")
    args = p.parse_args()

    start_typing(args.chat_id, args.orig_id)
    try:
        service = get_calendar_service()

        now = datetime.now(JERUSALEM)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=args.days)).isoformat()

        result = service.events().list(
            calendarId=args.calendar,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=MAX_EVENTS,
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        events = result.get("items", [])

        if not events:
            finish(args, f"{RLM}📅 אין אירועים ב-{args.days} הימים הקרובים.")
            return

        lbl = local_tz.tz_label()
        lines = [f"{RLM}📅 {len(events)} אירועים ב-{args.days} הימים הקרובים" + (f" (שעון {lbl})" if lbl else "") + ":"]
        lines.append(f"{RLM}{'─' * 28}")
        for event in events:
            lines.append(format_event_for_display(event))

        action_log("fast_cal_list", "list", f"days={args.days} count={len(events)}")
        finish(args, "\n".join(lines))
    except FileNotFoundError as e:
        fail(args, f"יומן לא מחובר — {e}")
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")


if __name__ == "__main__":
    main()
