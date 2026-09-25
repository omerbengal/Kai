"""
Execute (or cancel) an outward send drafted during a live voice conversation.

The voice server's delegate never sends to other people itself: it returns a SEND_PROPOSAL, the
server saves it to tmp/voice_pending_sends/<id>.json and posts it to Telegram with
voice_send_<id> / voice_cancel_<id> buttons. The dispatcher runs this script on the tap.

Usage:
  python voice_send_proposal.py send <id>
  python voice_send_proposal.py cancel <id>
Prints one line for the Telegram reply. Exit 0 = done, 1 = failed / not found.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "D:/Kai/scripts/fast")
from _base import action_log, find_wa_chat_jid, whatsapp_send  # noqa: E402

PENDING = Path("D:/Kai/tmp/voice_pending_sends")


def send_email(to, subject, body):
    """Gmail send via fast_email's helpers (not its main(), which replies to Telegram itself)."""
    import base64
    from fast_email import build_message, load_credentials
    creds, err = load_credentials()
    if err:
        return False, err
    msg, err = build_message([to], [], subject, body, [])
    if err:
        return False, err
    try:
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception as e:
        return False, str(e)[:300]
    return True, to


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("send", "cancel"):
        print(__doc__)
        return 1
    action, pid = sys.argv[1], sys.argv[2]
    path = PENDING / f"{pid}.json"
    if not path.exists():
        # Already sent or cancelled: a second tap must never send twice.
        print("\u200F⚠️ הטיוטה הזאת כבר טופלה (נשלחה או בוטלה)")
        return 1
    prop = json.loads(path.read_text(encoding="utf-8"))
    to, text, channel = prop["to"], prop["text"], prop.get("channel", "whatsapp")

    if action == "cancel":
        path.unlink()
        action_log("voice_send", "cancelled", f"id={pid} to={to}")
        print(f"\u200F❌ בוטל, ההודעה ל{to} לא נשלחה")
        return 0

    if channel == "email":
        ok, detail = send_email(to, prop.get("subject", ""), text)
    else:
        if "@" in to:
            jid = to
        else:
            jid, info = find_wa_chat_jid(to)
            if jid is None:
                print(f"\u200F❌ לא מצאתי את {to} בוואטסאפ ({info}). הטיוטה נשמרה, אפשר לנסות שוב.")
                return 1
        ok, detail = whatsapp_send(jid, text), jid

    if not ok:
        print(f"\u200F❌ השליחה ל{to} נכשלה ({detail}). הטיוטה נשמרה, אפשר לנסות שוב.")
        return 1
    path.unlink()
    action_log("voice_send", "sent", f"id={pid} channel={channel} to={to}")
    print(f"\u200F✅ נשלח ל{to}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
