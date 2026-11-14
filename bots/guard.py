#!/usr/bin/env python
"""PreToolUse guard for a Kai domain bot (separation step 1, 05/11/2026).

Wired in the bot's .claude/settings.json as
    PreToolUse (all tools): python D:/Kai/bots/guard.py --bot <name>
Exit 2 = the tool call is blocked, the reason (stderr) goes back to the model. Every block is
logged to <home>/bot.db table guard_log. Design: docs/multi_bot_separation_design.md, section 1 (L2).

What it blocks for a bot whose config has no "whatsapp" capability:
  - any WhatsApp access: MCP whatsapp tools, the bridge port, its DB files, its folder, WhatsApp scripts, and
    (06/11/2026) the bus too: such a bot asks the WhatsApp bot visibly, with a General tag (bot_tg.py ask)
  - main Kai's private state: D:/Kai/.claude, the main Telegram token / state, main memory,
    the main-token Telegram scripts (fast/*, dispatch_start, tg_silent_send, tg_pin, log_ack...),
    restart / plugin patch scripts
  - other bots' homes, and edits to its own guard / settings / bots.json
  - writes to the shared contacts (people, people_notes): read-only for every bot except the owner

Honest limit: it sees the TEXT of the tool call. A script that reads messages.db internally passes
when its command line does not name the file; that is what step 3 (bridge token + gateway) closes.
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, "D:/Kai/scripts")
sys.path.insert(0, "D:/Kai/bots")
import kai_bots  # noqa: E402

WHATSAPP = [
    r"[:/]8080\b", r"\bmessages\.db\b", r"\bwhatsapp\.db\b", r"whatsapp-bridge", r"whatsapp-mcp",
    r"/api/send\b", r"/api/download\b",
]
MAIN_STATE = [
    r"d:/kai/\.claude(/|$|\b)", r"\.claude/channels/telegram", r"projects/d--kai/memory",
    r"<REDACTED_SECRET>",  # main bot token id
    r"restart_claude", r"patch_telegram_plugin", r"scripts/fast/", r"\bfast_[a-z_]+\.py",
    r"dispatch_start\.py", r"tg_silent_send", r"\btg_pin\.py", r"\blog_ack\.py", r"\bstale_ack\.py",
    r"scripts/auto_ack\.py", r"session_start_cleanup", r"typing_lease", r"start_typing", r"stop_typing",
]
# Bus (stage 2, 05/11/2026): approvals are written by the approve-hook (code) and the executor runs as its
# own task; no model may run either, write the bus DB, or send WhatsApp outside the bus - not even the
# WhatsApp bot (its sends go through `bus.py draft` + Omer's button too).
BUS_CODE_ONLY = [
    r"bus\.py\W+(approve-hook|serve)\b", r"kaibots/_bus", r"\bbus\.db\b",
    r"/api/send\b", r"\bwa\.send\b", r"\bsend_media\b", r"\bwhatsapp_send", r"\bimport\s+bus\b", r"from\s+bus\s+import",
]
NO_WA_EXTRA = [r"bots/wa\.py", r"\bimport\s+wa\b", r"from\s+wa\s+import",
               r"[a-z_]*_wa\.py\b", r"\bwhatsapp_[a-z_]+\.py\b"]
# 06/11/2026 (Omer msgs 358 / 361, "חלוקת תפקידים טהורה"): a bot without WhatsApp has no hidden bus path
# either. It asks the WhatsApp bot visibly, by tagging it in General (bot_tg.py ask).
NO_WA_BUS = [r"bus\.py\W+(read|search|draft|wait|status|policy)\b"]
ASK_HINT = ("WhatsApp is the WhatsApp bot's only. Ask it in General: "
            "python D:/Kai/bots/bot_tg.py ask --bot <you> --to whatsapp --text \"<request>\"")
IDENTITY_RE = re.compile(r"bus\.py\b.*?--(?:from|bot)[\s=]+[\"']?([a-z_]+)")
BOT_TG_IDENTITY_RE = re.compile(r"bot_tg\.py\b.*?--bot[\s=]+[\"']?([a-z_]+)")
SELF_PROTECT = [r"bots/guard\.py", r"config/bots\.json", r"\.claude/settings(\.local)?\.json", r"\.mcp\.json"]
SELF_PROTECT_FILES = SELF_PROTECT + [r"d:/kai/bots/"]  # Write/Edit only: the shell must still run bots/bot_tg.py
CONTACT_WRITE = re.compile(
    r"\b(insert\s+(or\s+\w+\s+)?into|replace\s+into|update|delete\s+from)\s+[\"'`\[]?people(_notes)?\b",
    re.IGNORECASE)

RELEASE_CODE_ONLY = [r"release_ok\.json", r"release_gate\.py\W+hook"]
# Claude's scratchpad / temp: always writable, also for a bot with write_roots
SCRATCH_ROOTS = ["/appdata/local/temp/"]

SHELL_TOOLS = {"Bash", "PowerShell"}
FILE_TOOLS = {"Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep"}
WRITE_TOOLS = {"Write", "Edit", "NotebookEdit"}


def norm(s):
    return (s or "").replace("\\\\", "/").replace("\\", "/").lower()


def caps(cfg):
    return set(cfg.get("caps", []))


def other_homes(name, config_path=kai_bots.CONFIG):
    out = []
    for n, c in kai_bots.load_config(config_path).items():
        if n != name and c.get("home"):
            out.append(re.escape(norm(c["home"]).rstrip("/")) + r"(/|$|\b)")
    return out


def check(name, tool, tool_input, config_path=kai_bots.CONFIG):
    """None = allowed, else the reason string."""
    cfg = kai_bots.bot(name, config_path)
    has_wa = "whatsapp" in caps(cfg)
    t = tool or ""
    if t.startswith("mcp__whatsapp") and not has_wa:
        return ASK_HINT
    if t in SHELL_TOOLS:
        text = norm(tool_input.get("command", ""))
        is_write = True
    elif t in FILE_TOOLS:
        text = norm(" ".join(str(tool_input.get(k, "")) for k in ("file_path", "notebook_path", "path", "pattern")))
        is_write = t in WRITE_TOOLS
    else:
        return None
    rules = []
    if not has_wa:
        rules += [(p, "WhatsApp is the WhatsApp bot's only (bridge, its DBs, its scripts)") for p in WHATSAPP]
        rules += [(p, ASK_HINT) for p in NO_WA_EXTRA + NO_WA_BUS]
    rules += [(p, "bus approvals / executor / sends are code-only (Omer's button + KaiBusServe)")
              for p in BUS_CODE_ONLY]
    if t in SHELL_TOOLS:
        for who in IDENTITY_RE.findall(text):
            if who != name:
                return f"a bot may use the bus only as itself (--from/--bot {name}, not {who})"
        for who in BOT_TG_IDENTITY_RE.findall(text):
            if who != name:
                return f"a bot may use bot_tg.py only as itself (--bot {name}, not {who})"
    rules += [(p, "main Kai's private state / main-token scripts") for p in MAIN_STATE]
    rules += [(p, "another bot's home") for p in other_homes(name, config_path)]
    if is_write:
        own = SELF_PROTECT_FILES if t in WRITE_TOOLS else SELF_PROTECT
        rules += [(p, "the bot's own guard / settings / config are not editable by the bot") for p in own]
    rules += [(p, "blocked for this bot by config/bots.json blocked_patterns") for p in cfg.get("blocked_patterns", [])]
    # 14/11/2026 (appdev): Omer's merge approval is written only by the release_gate hook (his button)
    rules += [(p, "release approvals are code-only (Omer's relok button)") for p in RELEASE_CODE_ONLY]
    for pat, why in rules:
        if re.search(pat, text):
            return f"{why} (matched {pat!r})"
    if t in WRITE_TOOLS and cfg.get("write_roots"):
        target = norm(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        target = re.sub(r"^/([a-z])/", r":/", target)  # git-bash /d/... = d:/...
        roots = [norm(r).rstrip("/") + "/" for r in cfg["write_roots"]]
        if ".." in target or not (any(target.startswith(r) for r in roots)
                                  or any(r in target for r in SCRATCH_ROOTS)):
            return f"this bot writes only inside {', '.join(cfg['write_roots'])} (config/bots.json write_roots)"
    gate = cfg.get("release_gate") or {}
    if t in SHELL_TOOLS and gate:
        import release_gate
        hit = next((pat for pat in gate.get("patterns", []) if re.search(pat, text)), None)
        if not hit and gate.get("push_to_main") and release_gate.push_needs_gate(text):
            hit = "git push to main (or a bare / unparsable push)"
        if hit and not release_gate.fresh(cfg):
            return ("merge to main / deploy needs Omer's approval button first: send the review with inline "
                    "buttons callback_data relok:<slug> / relno:<slug> and wait for his press (valid "
                    f"{gate.get('window_min', 30)} min). A feature branch may be pushed explicitly: "
                    f"git push -u origin <branch> (matched {hit!r})")
    if t in SHELL_TOOLS and "contacts_owner" not in caps(cfg) and CONTACT_WRITE.search(text):
        return "contacts (people / people_notes) are read-only for this bot; main Kai owns contact changes"
    return None


def log(name, tool, reason, tool_input):
    if os.environ.get("KAI_GUARD_NOLOG"):  # tests
        return
    try:
        conn = sqlite3.connect(os.path.join(kai_bots.bot(name)["home"], "bot.db"))
        conn.execute("CREATE TABLE IF NOT EXISTS guard_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,"
                     " tool TEXT, reason TEXT, input TEXT)")
        conn.execute("INSERT INTO guard_log (ts, tool, reason, input) VALUES (?,?,?,?)",
                     (datetime.now(timezone.utc).isoformat(), tool, reason,
                      json.dumps(tool_input, ensure_ascii=False)[:2000]))
        conn.commit()
        conn.close()
    except Exception:
        pass


def main(argv):
    name = argv[argv.index("--bot") + 1] if "--bot" in argv else None
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
    except Exception:
        return 0
    tool, ti = data.get("tool_name", ""), data.get("tool_input", {}) or {}
    try:
        reason = check(name, tool, ti)
    except Exception as e:  # a broken config must not open everything: fail closed for shell/file tools
        reason = f"guard error ({type(e).__name__}); blocked to be safe" if tool in SHELL_TOOLS | FILE_TOOLS else None
    if not reason:
        return 0
    log(name, tool, reason, ti)
    sys.stderr.buffer.write(f"[guard:{name}] BLOCKED {tool}: {reason}\n".encode("utf-8"))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
