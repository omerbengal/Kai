#!/usr/bin/env python3
"""
Fast WhatsApp history — last N messages with a contact.
Usage: python fast_wa_history.py --name "שם" --count 20 --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
import local_tz  # home timezone (travel-mode aware) - scripts/local_tz.py
import sqlite3
from datetime import datetime, timezone, timedelta


def main():
    p = base_args("Show WhatsApp history with contact")
    p.add_argument("--name", required=True, help="Contact name")
    p.add_argument("--count", type=int, default=20, help="Number of messages to show (default: 20)")
    args = p.parse_args()

    start_typing(args.chat_id, args.orig_id)
    try:
        jid, info = find_wa_chat_jid(args.name)

        if jid is None:
            kind, data = info
            if kind == "ambiguous":
                names_list = "\n".join(f"{RLM}• {n}" for n in sorted(data))
                fail(args, f'מצאתי כמה אפשרויות בשם "{args.name}":\n{names_list}\n{RLM}אנא פרט את השם המלא.')
            else:
                fail(args, f'לא נמצאה שיחת וואטסאפ עם "{args.name}"')
            return

        display_name = info

        wa = sqlite3.connect(WA_DB)
        wa.row_factory = sqlite3.Row
        msgs = wa.execute("""
            SELECT content, timestamp, is_from_me
            FROM messages
            WHERE chat_jid = ? AND content IS NOT NULL AND content != ''
            ORDER BY timestamp DESC
            LIMIT ?
        """, (jid, args.count)).fetchall()
        wa.close()

        if not msgs:
            fail(args, f'לא נמצאו הודעות עם "{display_name}"')
            return

        msgs = list(reversed(msgs))

        lines = [f"{RLM}💬 {len(msgs)} הודעות אחרונות עם {display_name}:"]
        lines.append(f"{RLM}{'─' * 28}")

        for m in msgs:
            ts = m["timestamp"]
            try:
                if isinstance(ts, str):
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                else:
                    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
                dt_jlm = dt.astimezone(local_tz.home_tz())
                time_str = dt_jlm.strftime("%d/%m %H:%M")
            except Exception:
                time_str = str(ts)

            who = "אני" if m["is_from_me"] else display_name
            content = (m["content"] or "").strip()[:200]
            lines.append(f"{RLM}[{time_str}] {who}: {content}")

        action_log("fast_wa_history", "history", f"name={args.name} jid={jid} count={len(msgs)}")
        finish(args, "\n".join(lines))
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")


if __name__ == "__main__":
    main()
