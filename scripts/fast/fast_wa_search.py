#!/usr/bin/env python3
"""
Fast WhatsApp search — find messages containing a keyword in a conversation.
Usage: python fast_wa_search.py --name "שם" --keyword "מילה" --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

Omit --name to search across ALL conversations.
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
import sqlite3
from datetime import datetime, timezone, timedelta

MAX_RESULTS = 15


def format_ts(ts):
    try:
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        dt_jlm = dt.astimezone(timezone(timedelta(hours=3)))
        return dt_jlm.strftime("%d/%m %H:%M")
    except Exception:
        return str(ts)


def main():
    p = base_args("Search WhatsApp conversation for keyword")
    p.add_argument("--name", default=None, help="Contact or group name (omit = all chats)")
    p.add_argument("--keyword", required=True, help="Word or phrase to search for")
    args = p.parse_args()

    start_typing(args.chat_id, args.orig_id)
    try:
        wa = sqlite3.connect(WA_DB)
        wa.row_factory = sqlite3.Row

        if args.name:
            jid, info = find_wa_chat_jid(args.name)

            if jid is None:
                wa.close()
                kind, data = info
                if kind == "ambiguous":
                    names_list = "\n".join(f"{RLM}• {n}" for n in sorted(data))
                    fail(args, f'מצאתי כמה אפשרויות בשם "{args.name}":\n{names_list}\n{RLM}אנא פרט את השם המלא.')
                else:
                    fail(args, f'לא נמצאה שיחת וואטסאפ עם "{args.name}"')
                return

            display_name = info

            msgs = wa.execute("""
                SELECT m.content, m.timestamp, m.is_from_me
                FROM messages m
                WHERE m.chat_jid = ? AND m.content LIKE ?
                ORDER BY m.timestamp DESC
                LIMIT ?
            """, (jid, f"%{args.keyword}%", MAX_RESULTS)).fetchall()
            scope_label = f"בשיחה עם {display_name}"
        else:
            msgs = wa.execute("""
                SELECT m.content, m.timestamp, m.is_from_me, c.name as chat_name, m.chat_jid
                FROM messages m
                JOIN chats c ON m.chat_jid = c.jid
                WHERE m.content LIKE ?
                ORDER BY m.timestamp DESC
                LIMIT ?
            """, (f"%{args.keyword}%", MAX_RESULTS)).fetchall()
            display_name = None
            scope_label = "בכל השיחות"

        wa.close()

        if not msgs:
            fail(args, f'לא נמצאו הודעות עם "{args.keyword}" {scope_label}')
            return

        lines = [f'{RLM}🔍 {len(msgs)} תוצאות ל-"{args.keyword}" {scope_label}:']
        lines.append(f"{RLM}{'─' * 28}")

        for m in msgs:
            time_str = format_ts(m["timestamp"])
            who = "אני" if m["is_from_me"] else (display_name or m.get("chat_name") or "?")
            content = (m["content"] or "").strip()[:200]
            chat_prefix = f"[{m['chat_name']}] " if not args.name and "chat_name" in m.keys() else ""
            lines.append(f"{RLM}{chat_prefix}[{time_str}] {who}: {content}")

        action_log("fast_wa_search", "search", f"name={args.name} keyword={args.keyword} results={len(msgs)}")
        finish(args, "\n".join(lines))
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")


if __name__ == "__main__":
    main()
