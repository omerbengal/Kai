#!/usr/bin/env python3
"""
Fast contact lookup — show full contact info by name.
Usage: python fast_contacts_lookup.py --name "שם" --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *

def format_birthday(bday):
    if not bday:
        return None
    # Stored as YYYY-MM-DD or similar
    return str(bday)

FIELD_LABELS = {
    "phone": ("📞", "phone"),
    "email": ("📧", "email"),
    "birthday": ("🎂", "birthday"),
    "telegram": ("💬", "telegram_username"),
}

def main():
    p = base_args("Look up contact info")
    p.add_argument("--name", required=True, help="Contact name to look up")
    p.add_argument("--fields", default="all",
                   help="Comma-separated fields to show: phone,email,birthday,telegram,notes,all (default: all)")
    args = p.parse_args()
    requested = [f.strip().lower() for f in args.fields.split(",")]
    show_all = "all" in requested

    start_typing(args.chat_id, args.orig_id)
    try:
        # Check for ambiguous matches first
        matches = find_ambiguous_matches(args.name)
        if len(matches) > 1:
            names_list = "\n".join(f"{RLM}• {m[1]}" for m in sorted(matches, key=lambda x: x[1]))
            fail(args, f'מצאתי כמה אנשים בשם "{args.name}":\n{names_list}\n{RLM}אנא פרט את השם המלא.')
            return

        # Resolve name
        person = resolve_name(args.name)

        lines = []

        if person and person.get("id"):
            # Found in people table — fetch full details
            person_id = person["id"]
            conn = get_db()

            row = conn.execute(
                "SELECT name, phone, email, birthday, telegram_username FROM people WHERE id=?",
                (person_id,)
            ).fetchone()

            if row:
                lines.append(f"{RLM}👤 {row['name']}")
                for field_key, (icon, col) in FIELD_LABELS.items():
                    if (show_all or field_key in requested) and row[col]:
                        val = row[col]
                        if col == "birthday":
                            val = format_birthday(val)
                        if col == "telegram_username":
                            val = f"@{val}"
                        lines.append(f"{RLM}{icon} {val}")

                # Nicknames
                if show_all or "nicknames" in requested:
                    nicks = conn.execute(
                        "SELECT nickname FROM people_nicknames WHERE person_id=? ORDER BY id",
                        (person_id,)
                    ).fetchall()
                    if nicks:
                        lines.append(f"{RLM}🏷 כינויים: {', '.join(n[0] for n in nicks)}")

                # Notes
                if show_all or "notes" in requested:
                    notes = conn.execute("""
                        SELECT pn.note, nc.name as category
                        FROM people_notes pn
                        LEFT JOIN note_categories nc ON pn.category_id = nc.id
                        WHERE pn.person_id=?
                        ORDER BY nc.name, pn.id
                    """, (person_id,)).fetchall()

                    if notes:
                        lines.append(f"{RLM}")
                        lines.append(f"{RLM}📝 פתקים:")
                        for note in notes:
                            cat = note["category"] or "כללי"
                            lines.append(f"{RLM}• [{cat}] {note['note']}")

            conn.close()

        elif person and person.get("name"):
            # Found in phone_contacts (VCF)
            lines.append(f"{RLM}👤 {person['name']}")
            if person.get("phone"):
                lines.append(f"{RLM}📞 {person['phone']}")
            lines.append(f"{RLM}(מתוך אנשי קשר מיובאים)")

        else:
            fail(args, f'לא נמצא איש קשר בשם "{args.name}"')
            return

        if not lines:
            fail(args, f'לא נמצאו פרטים עבור "{args.name}"')
            return

        text = "\n".join(lines)
        action_log("fast_contacts_lookup", "lookup", f"name={args.name} resolved={person.get('name')}")
        finish(args, text)
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")

if __name__ == "__main__":
    main()
