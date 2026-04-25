#!/usr/bin/env python3
"""
Fast memory save — persist a fact to Kai's memory system.
Usage: python fast_memory_save.py --fact "עובדה לשמירה" --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}

Writes to: C:/Users/Omer/.claude/projects/D--Kai/memory/runtime_notes.md
And ensures MEMORY.md has a pointer to it.
"""
import sys
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *
import os
from datetime import datetime, timezone, timedelta

MEMORY_DIR = "C:/Users/Omer/.claude/projects/D--Kai/memory"
NOTES_FILE = f"{MEMORY_DIR}/runtime_notes.md"
MEMORY_INDEX = f"{MEMORY_DIR}/MEMORY.md"
NOTES_POINTER = "- [עובדות שנשמרו בזמן ריצה](runtime_notes.md) — עובדות שעומר ביקש לשמור במהלך שיחות"


def ensure_notes_file():
    """Create runtime_notes.md with frontmatter if it doesn't exist."""
    if not os.path.exists(NOTES_FILE):
        header = (
            "---\n"
            "name: עובדות שנשמרו בזמן ריצה\n"
            "description: עובדות שעומר ביקש לשמור במהלך שיחות, דרך fast_memory_save\n"
            "type: user\n"
            "---\n\n"
            "# עובדות שנשמרו\n\n"
        )
        with open(NOTES_FILE, "w", encoding="utf-8") as f:
            f.write(header)


def ensure_memory_index_pointer():
    """Add pointer to MEMORY.md if not already there."""
    if not os.path.exists(MEMORY_INDEX):
        return
    with open(MEMORY_INDEX, "r", encoding="utf-8") as f:
        content = f.read()
    if "runtime_notes.md" not in content:
        with open(MEMORY_INDEX, "a", encoding="utf-8") as f:
            f.write(f"\n{NOTES_POINTER}\n")


def append_fact(fact):
    """Append fact with timestamp to notes file."""
    dt_jlm = datetime.now(timezone.utc) + timedelta(hours=3)
    date_str = dt_jlm.strftime("%Y-%m-%d %H:%M")
    line = f"- [{date_str}] {fact}\n"
    with open(NOTES_FILE, "a", encoding="utf-8") as f:
        f.write(line)


def main():
    p = base_args("Save a memory fact")
    p.add_argument("--fact", required=True, help="The fact to remember")
    args = p.parse_args()

    start_typing(args.chat_id, args.orig_id)
    try:
        ensure_notes_file()
        ensure_memory_index_pointer()
        append_fact(args.fact)

        action_log("fast_memory_save", "saved", f"fact={args.fact[:80]}")
        finish(args, f"{RLM}✅ נשמר בזיכרון:\n{RLM}📝 {args.fact}")
    except Exception as e:
        fail(args, f"שגיאה פנימית: {e}")


if __name__ == "__main__":
    main()
