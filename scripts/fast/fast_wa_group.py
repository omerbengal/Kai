#!/usr/bin/env python3
"""
Fast WhatsApp send to a group by name.
Supports text, images, audio, and files.

Usage:
  # Text only
  python fast_wa_group.py --group "שם קבוצה" --message "טקסט" --ack_id N --orig_id N

  # File only (no caption)
  python fast_wa_group.py --group "שם קבוצה" --file "/abs/path/chart.png" --ack_id N --orig_id N

  # File with caption
  python fast_wa_group.py --group "שם קבוצה" --file "/abs/path/chart.png" --message "כיתוב" --ack_id N --orig_id N

At least one of --message or --file must be provided.

Resolution priority:
1. wa_groups table in kai.db (curated favorites, fuzzy match)
2. chats table in messages.db (all synced groups, fuzzy match)
"""
import sys
import os
import json
import sqlite3
import urllib.request

sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *

WA_DB = 'D:/Kai/whatsapp-mcp/whatsapp-bridge/store/messages.db'
WA_API = 'http://localhost:8080/api/send'
SUFFIX = "\n[נשלח על ידי קאי 🤖]"
FUZZY_THRESHOLD = 60


def fuzzy_score(a, b):
    try:
        from rapidfuzz import fuzz
        return fuzz.partial_ratio(a.lower(), b.lower())
    except ImportError:
        import difflib
        return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100


def best_fuzzy_match(query, candidates):
    """
    candidates: list of (jid, name)
    Returns list of (score, jid, name) sorted desc, filtered by threshold.
    """
    scored = []
    for jid, name in candidates:
        if not name:
            continue
        score = fuzzy_score(query, name)
        if score >= FUZZY_THRESHOLD:
            scored.append((score, jid, name))
    scored.sort(reverse=True)
    return scored


def pick_match(scored):
    """
    From scored list, return (jid, name) if unambiguous, or (None, error_info).
    """
    if not scored:
        return None, ("not_found",)
    best_score = scored[0][0]
    close = [s for s in scored if s[0] >= best_score - 10]
    unique_names = list(dict.fromkeys(s[2] for s in close))
    if len(unique_names) == 1:
        return close[0][1], close[0][2]
    return None, ("ambiguous", unique_names)


def find_group_jid(query):
    """
    Resolve group name → (jid, display_name) or (None, error_info).
    Checks wa_groups (favorites) first, then falls back to chats DB.
    """
    # Step 1: wa_groups favorites (kai.db)
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = str
    fav_rows = conn.execute("SELECT jid, name FROM wa_groups").fetchall()
    conn.close()

    if fav_rows:
        scored = best_fuzzy_match(query, fav_rows)
        if scored:
            return pick_match(scored)

    # Step 2: chats DB (all synced groups)
    wa = sqlite3.connect(WA_DB)
    wa.text_factory = str
    chat_rows = wa.execute(
        "SELECT jid, name FROM chats WHERE jid LIKE '%g.us%' ORDER BY last_message_time DESC"
    ).fetchall()
    wa.close()

    scored = best_fuzzy_match(query, chat_rows)
    return pick_match(scored)


def whatsapp_send_group(jid, message):
    """Send a WhatsApp text message to a JID. Returns (success, status_msg)."""
    payload = json.dumps({"recipient": jid, "message": message}, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        WA_API,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"}
    )
    resp = urllib.request.urlopen(req, timeout=15).read()
    data = json.loads(resp)
    return data.get("success", False), data.get("message", "")


def main():
    p = base_args("Send WhatsApp to a group")
    p.add_argument("--group", required=True, help="Group name (fuzzy match)")
    p.add_argument("--message", default=None, help="Message text (optional when --file is given)")
    p.add_argument("--file", default=None, dest="file_path",
                   help="Absolute path to file (image/audio/document) to send")
    args = p.parse_args()

    # Validate: at least one of --message or --file
    if not args.message and not args.file_path:
        p.error("At least one of --message or --file must be provided.")

    # Validate file exists
    if args.file_path and not os.path.isfile(args.file_path):
        p.error(f"File not found: {args.file_path}")

    start_typing(args.chat_id, args.orig_id)
    try:
        jid, info = find_group_jid(args.group)

        if jid is None:
            kind = info[0]
            if kind == "ambiguous":
                names_list = "\n".join(f"{RLM}• {n}" for n in info[1])
                fail(args, f'מצאתי כמה קבוצות:\n{names_list}\n{RLM}אנא פרט את השם.')
            else:
                fail(args, f'לא נמצאה קבוצה בשם "{args.group}"')
            return

        group_name = info

        if args.file_path:
            # Send file (with optional caption)
            caption = (args.message + SUFFIX) if args.message else None
            success = whatsapp_send_file(jid, args.file_path, caption=caption)
            file_name = os.path.basename(args.file_path)
            if success:
                action_log("fast_wa_group", "sent_file", f"group={group_name} jid={jid} file={file_name}")
                if args.message:
                    preview = args.message[:50] + ('...' if len(args.message) > 50 else '')
                    finish(args, f'{RLM}✅ נשלח לקבוצה "{group_name}":\n{RLM}📎 {file_name}\n{RLM}"{preview}"')
                else:
                    finish(args, f'{RLM}✅ נשלח לקבוצה "{group_name}":\n{RLM}📎 {file_name}')
            else:
                fail(args, f'שליחת הקובץ לקבוצה "{group_name}" נכשלה')
        else:
            # Text only (existing behavior)
            full_message = args.message + SUFFIX
            success, status = whatsapp_send_group(jid, full_message)
            if success:
                preview = args.message[:50] + ('...' if len(args.message) > 50 else '')
                action_log("fast_wa_group", "sent", f"group={group_name} jid={jid}")
                finish(args, f'{RLM}✅ נשלח לקבוצה "{group_name}":\n{RLM}"{preview}"')
            else:
                fail(args, f'שליחת וואטסאפ לקבוצה "{group_name}" נכשלה: {status}')

    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")


if __name__ == "__main__":
    main()
