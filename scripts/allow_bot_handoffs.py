#!/usr/bin/env python
"""Let the domain bots' General tags of main Kai reach main Kai (07/11/2026, Omer msgs 842 / 847).

Main Kai's Telegram plugin drops every General message whose sender is not in the group's `allowFrom`
(today: Omer only). The bot -> main handoff (`bots/bot_tg.py ask --to main`) needs the domain bots' ids there
too. Main's auto-ACK hook then ignores any bot line that does not tag main Kai (`reason=bot_chatter`) and
rate-limits the tags (scripts/auto_ack.py, BOT_HANDOFF_MAX_PER_HOUR).

This edits main Kai's channel access, so Omer runs it himself (never an agent on a Telegram request):
  python D:/Kai/scripts/allow_bot_handoffs.py            show what would change
  python D:/Kai/scripts/allow_bot_handoffs.py --apply    write it (backup next to the file)
  python D:/Kai/scripts/allow_bot_handoffs.py --revert   remove the bot ids again
Only the bots group's entry changes (topics / requireMention / DMs untouched). The plugin re-reads the file
on every message: no restart needed.
"""
import io
import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kai_bots  # noqa: E402

ACCESS = os.path.expanduser("~/.claude/channels/telegram/access.json")


def bot_ids(bots=None):
    bots = bots if bots is not None else kai_bots.load_config()
    return sorted(str(c["bot_id"]) for c in bots.values() if c.get("bot_id") and c.get("enabled") is not False)


def change(access, group, ids, revert=False):
    g = access.get("groups", {}).get(group)
    if g is None:
        raise SystemExit(f"group {group} is not in access.json")
    allow = [str(x) for x in g.get("allowFrom", [])]
    new = [x for x in allow if x not in ids] if revert else allow + [x for x in ids if x not in allow]
    g["allowFrom"] = new
    return allow, new


def main(argv):
    group = str(next(iter(kai_bots.load_config().values()))["group_id"])
    with open(ACCESS, encoding="utf-8") as f:
        access = json.load(f)
    before, after = change(access, group, bot_ids(), revert="--revert" in argv)
    print(f"group {group} allowFrom: {before} -> {after}")
    if before == after:
        print("nothing to change")
        return 0
    if "--apply" not in argv and "--revert" not in argv:
        print("dry run; add --apply to write it")
        return 0
    shutil.copy2(ACCESS, ACCESS + ".bak-" + datetime.now().strftime("%Y%m%d%H%M%S"))
    tmp = ACCESS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(access, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ACCESS)
    print("written")
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.exit(main(sys.argv))
