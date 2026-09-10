#!/usr/bin/env python3
"""
Fast WhatsApp group creation.

Usage:
  # Create group only
  python fast_wa_group_create.py --name "קבוצת בדיקה" --participants "<contact>,<contact> <contact>,<contact>" --ack_id N --orig_id N

  # Create group AND send initial message
  python fast_wa_group_create.py --name "קבוצת בדיקה" --participants "<contact>,<contact>,גיא" --message "היי חברים" --ack_id N --orig_id N

Resolves each participant via resolve_name.py. If any participant can't be resolved
(score < THRESHOLD or null), aborts before creating the group and tells Omer who's missing.
"""
import sys
import os
import json
import urllib.request

sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import (
    base_args, start_typing, stop_typing, finish, fail,
    resolve_name, action_log, whatsapp_send,
    RLM, WHATSAPP_API,
)

GROUP_CREATE_URL = f"{WHATSAPP_API}/api/group/create"
SUFFIX = "\n[נשלח על ידי קאי 🤖]"


def _ensure_signature(text):
    if not text:
        return text
    if text.rstrip().endswith("[נשלח על ידי קאי 🤖]"):
        return text
    return text + SUFFIX


def _normalize_phone(raw):
    """Strip +, spaces, dashes. Return digits only."""
    if not raw:
        return None
    return str(raw).replace("+", "").replace("-", "").replace(" ", "")


def create_group(name, participant_phones):
    """POST to /api/group/create. Returns (success, jid_or_error)."""
    payload = json.dumps(
        {"name": name, "participants": participant_phones},
        ensure_ascii=False
    ).encode("utf-8")
    req = urllib.request.Request(
        GROUP_CREATE_URL,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"}
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30).read()
        data = json.loads(resp)
        if data.get("success"):
            return True, data.get("jid", "")
        return False, data.get("message", "unknown bridge error")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
            return False, f"HTTP {e.code}: {body[:200]}"
        except Exception:
            return False, f"HTTP {e.code}"
    except Exception as e:
        return False, f"connection error: {e}"


def main():
    p = base_args("Create WhatsApp group")
    p.add_argument("--name", required=True, help="Group name (max 25 chars per WhatsApp)")
    p.add_argument("--participants", required=True,
                   help='Comma-separated participant names (e.g. "<contact>,<contact> <contact>,<contact>")')
    p.add_argument("--message", default=None,
                   help="Optional initial message to send to the new group after creation")
    args = p.parse_args()

    if len(args.name) > 25:
        # WhatsApp truncates / rejects long group names — warn early.
        # 25 chars is the documented whatsmeow limit.
        pass  # let the bridge return the 406 if it fails; for now just continue.

    start_typing(args.chat_id, args.orig_id)
    try:
        names = [n.strip() for n in args.participants.split(",") if n.strip()]
        if not names:
            fail(args, "לא ציינת אף משתתף.")
            return

        # ── Step 1: resolve every participant ─────────────────────────────
        resolved = []  # list of (display, phone)
        missing = []   # list of names we couldn't resolve
        for n in names:
            res = resolve_name(n)
            if not res or not res.get("name") or not res.get("phone"):
                missing.append(n)
                continue
            phone = _normalize_phone(res.get("phone"))
            if not phone:
                missing.append(n)
                continue
            resolved.append((res["name"], phone))

        if missing:
            missing_list = ", ".join(f'"{m}"' for m in missing)
            fail(args, f'לא הצלחתי לאתר: {missing_list}. ציין מספר טלפון או שם מדויק כפי שמופיע ב-WhatsApp.')
            return

        # ── Step 2: create the group ──────────────────────────────────────
        phones = [phone for (_disp, phone) in resolved]
        ok, jid_or_err = create_group(args.name, phones)
        if not ok:
            fail(args, f'יצירת הקבוצה "{args.name}" נכשלה: {jid_or_err}')
            return

        group_jid = jid_or_err
        display_names = ", ".join(d for d, _ in resolved)
        action_log("fast_wa_group_create", "created",
                   f'name="{args.name}" jid={group_jid} participants={len(resolved)}')

        # ── Step 3: optionally send initial message ───────────────────────
        if args.message:
            full = _ensure_signature(args.message)
            try:
                sent = whatsapp_send(group_jid, full)
            except Exception as e:
                sent = False
                send_err = str(e)
            if not sent:
                # Group was created but message failed. Tell Omer both facts.
                finish(args,
                       f'{RLM}✅ קבוצה "{args.name}" נוצרה עם {display_names}, '
                       f'{RLM}אך שליחת ההודעה הראשונה נכשלה. אפשר לשלוח ידנית.')
                return
            action_log("fast_wa_group_create", "sent_initial",
                       f'jid={group_jid} preview={args.message[:50]}')
            preview = args.message[:80] + ('...' if len(args.message) > 80 else '')
            finish(args,
                   f'{RLM}✅ יצרתי "{args.name}" עם {display_names}\n'
                   f'{RLM}📤 ושלחתי: "{preview}"')
        else:
            finish(args, f'{RLM}✅ יצרתי קבוצה "{args.name}" עם {display_names}.')

    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")


if __name__ == "__main__":
    main()
