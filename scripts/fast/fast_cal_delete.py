#!/usr/bin/env python3
"""
Fast calendar delete — remove an upcoming event found by keyword.
Usage: python fast_cal_delete.py --search "פגישה" --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

Searches the next 30 days. Deletes the first (soonest) matching event.
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
import local_tz  # home timezone (travel-mode aware) - scripts/local_tz.py
from _gcal import get_calendar_service, format_event_for_display
from datetime import datetime, timezone, timedelta

JERUSALEM = local_tz.home_tz()  # was fixed UTC+3; travel-mode aware


def main():
    p = base_args("Delete a calendar event")
    p.add_argument("--search", required=True, help="Keyword to find the event")
    p.add_argument("--calendar", default="primary", help="Calendar ID (default: primary)")
    args = p.parse_args()

    start_typing(args.chat_id, args.orig_id)
    try:
        service = get_calendar_service()

        now = datetime.now(JERUSALEM).isoformat()
        far = (datetime.now(JERUSALEM) + timedelta(days=365)).isoformat()

        result = service.events().list(
            calendarId=args.calendar,
            timeMin=now,
            timeMax=far,
            maxResults=50,
            singleEvents=True,
            orderBy="startTime",
            q=args.search
        ).execute()

        events = result.get("items", [])
        if not events:
            fail(args, f'לא נמצא אירוע עם "{args.search}" בשנה הקרובה')
            return

        event = events[0]
        event_id = event["id"]
        title = event.get("summary", "?")
        start = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", "?"))

        # Display start in Jerusalem time
        try:
            from datetime import datetime as dt_cls
            dt = dt_cls.fromisoformat(start.replace("Z", "+00:00"))
            dt_jlm = dt.astimezone(JERUSALEM)
            start_disp = dt_jlm.strftime("%d/%m %H:%M")
        except Exception:
            start_disp = start[:16]

        service.events().delete(
            calendarId=args.calendar,
            eventId=event_id
        ).execute()

        action_log("fast_cal_delete", "deleted", f"title={title} event_id={event_id}")
        finish(args, f"{RLM}🗑 אירוע נמחק:\n{RLM}📌 {title}\n{RLM}📅 {start_disp}")
    except FileNotFoundError as e:
        fail(args, f"יומן לא מחובר — {e}")
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")


if __name__ == "__main__":
    main()
