"""
Google Calendar API helper for fast scripts.
Uses OAuth2 with stored token at D:/Kai/credentials/google_token.json.

Run D:/Kai/scripts/fast/fast_cal_auth.py once to generate the token.
"""
import os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_PATH = "D:/Kai/credentials/google_token.json"
CLIENT_SECRET_PATH = "D:/Kai/credentials/google_client_secret.json"
TIMEZONE = "Asia/Jerusalem"


def get_calendar_service():
    """Return an authorized Google Calendar service object."""
    if not os.path.exists(TOKEN_PATH):
        raise FileNotFoundError(
            f"Token not found at {TOKEN_PATH}. "
            "Run python D:/Kai/scripts/fast/fast_cal_auth.py to authenticate."
        )

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed token
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
        else:
            raise RuntimeError(
                "Token is invalid and cannot be refreshed. "
                "Run python D:/Kai/scripts/fast/fast_cal_auth.py to re-authenticate."
            )

    return build("calendar", "v3", credentials=creds)


def parse_datetime_jerusalem(dt_str):
    """
    Parse a datetime string in Jerusalem time and return an RFC3339 string.
    Input format: 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD'
    Returns dict suitable for Google Calendar API event start/end.
    """
    from datetime import datetime
    dt_str = dt_str.strip()

    if len(dt_str) == 10:
        # Date only — all-day event
        return {"date": dt_str}
    else:
        # Has time — use Jerusalem timezone
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        except ValueError:
            dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")
        # Format as RFC3339 with +03:00 (Jerusalem DST)
        return {
            "dateTime": dt.strftime("%Y-%m-%dT%H:%M:%S") + "+03:00",
            "timeZone": TIMEZONE,
        }


RSVP_ICONS = {
    "accepted": "✅",
    "declined": "❌",
    "tentative": "🤔",
    "needsAction": "❓",
    "organizer": "",   # organizer = his own event, no icon needed
}


def _parse_dt_jerusalem(dt_str):
    """Parse an RFC3339 dateTime string and return a datetime in Jerusalem tz."""
    from datetime import datetime, timezone, timedelta
    JERUSALEM = timezone(timedelta(hours=3))
    if "+" in dt_str[10:] or dt_str.endswith("Z"):
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.astimezone(JERUSALEM)
    return datetime.fromisoformat(dt_str)


def _get_rsvp_status(event):
    """Return the organizer-or-attendee response status for this event."""
    organizer = event.get("organizer", {})
    if organizer.get("self"):
        return "organizer"
    attendees = event.get("attendees", [])
    self_entry = next((a for a in attendees if a.get("self")), None)
    if self_entry:
        return self_entry.get("responseStatus", "needsAction")
    # Invited but no attendees list — treat as needsAction
    return "needsAction"


def format_event_for_display(event):
    """Format a Google Calendar event for Telegram display."""
    RLM = "\u200F"
    title = event.get("summary", "(ללא כותרת)")
    start = event.get("start", {})
    end = event.get("end", {})
    location = event.get("location", "")

    rsvp = _get_rsvp_status(event)
    rsvp_icon = RSVP_ICONS.get(rsvp, "❓")

    if "dateTime" in start:
        try:
            dt_start = _parse_dt_jerusalem(start["dateTime"])
            date_display = dt_start.strftime("%d/%m %H:%M")
        except Exception:
            date_display = start["dateTime"][:16]

        # End time
        if "dateTime" in end:
            try:
                dt_end = _parse_dt_jerusalem(end["dateTime"])
                # Only show end time if same day, else show full date
                if dt_start.date() == dt_end.date():
                    date_display += f"–{dt_end.strftime('%H:%M')}"
                else:
                    date_display += f" → {dt_end.strftime('%d/%m %H:%M')}"
            except Exception:
                pass
    elif "date" in start:
        date_display = start["date"]
    else:
        date_display = "?"

    prefix = f"{rsvp_icon} " if rsvp_icon else ""
    line = f"{RLM}{prefix}📅 {date_display} — {title}"
    if location:
        line += f"\n{RLM}   📍 {location}"
    return line
