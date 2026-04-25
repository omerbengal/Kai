#!/usr/bin/env python3
"""
Fast contact creation — add a new contact to the people table.
Usage: python fast_contacts_create.py --name "שם מלא" [--phone "05X..."] [--email "<email>"]
       [--telegram_username "user"] --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

At least --name is required.
"""
import sys

sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *


def main():
    p = base_args("Add a new contact")
    p.add_argument("--name", required=True, help="Full name of the contact")
    p.add_argument("--phone", default=None, help="Phone number (e.g. <phone>)")
    p.add_argument("--email", default=None, help="Email address")
    p.add_argument("--telegram_username", default=None, help="Telegram username (without @)")
    args = p.parse_args()

    start_typing(args.chat_id, args.orig_id)
    try:
        conn = get_db()

        # Check for existing contacts with similar name
        existing = conn.execute(
            "SELECT id, name FROM people WHERE name LIKE ? LIMIT 3",
            (f'%{args.name}%',)
        ).fetchall()

        dup_warning = ''
        if existing:
            dups = ', '.join(f'{r["name"]} (id={r["id"]})' for r in existing)
            dup_warning = f'\n{RLM}⚠️ שים לב: קיים כבר {dups} במאגר'

        # Insert new contact
        conn.execute(
            """INSERT INTO people (name, phone, email, telegram_username, remind_birthday_days)
               VALUES (?, ?, ?, ?, 7)""",
            (args.name, args.phone or None, args.email or None, args.telegram_username or None)
        )
        conn.commit()

        # Verify insert
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        verified = conn.execute("SELECT id, name FROM people WHERE id = ?", (new_id,)).fetchone()
        conn.close()

        if verified:
            action_log("fast_contacts_create", "created", f"name={args.name} id={new_id}")
            phone_str = args.phone or '—'
            email_str = args.email or '—'
            tg_str = f'@{args.telegram_username}' if args.telegram_username else '—'
            msg = (
                f'{RLM}✅ איש קשר נוסף: {args.name}\n'
                f'{RLM}• טלפון: {phone_str}\n'
                f'{RLM}• אימייל: {email_str}\n'
                f'{RLM}• טלגרם: {tg_str}'
                f'{dup_warning}'
            )
            finish(args, msg)
        else:
            fail(args, f'הוספת איש קשר "{args.name}" נכשלה')
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")


if __name__ == "__main__":
    main()
