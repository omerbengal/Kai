#!/usr/bin/env python
"""Live ACK for domain-bot workers, in code (15/11/2026, appdev tag 3544 for Omer, topic msg 3540).

Omer thought the bots' workers were stuck: the pinned ACK showed the same step for many minutes, because
editing it before each step was only an instruction. Now the bot's own hooks do it:

  hook   PreToolUse (matcher *, async) in every bot session. A tool call made by a worker (the hook input
         has `agent_id`) edits that worker's open ACK to
             🔄 <TITLE> — ⏳ <step> · עודכן HH:MM
         where <step> is the Bash/PowerShell `description` (else a short label of the tool) and HH:MM is
         Omer's local time (scripts/local_tz.py). At most one edit per ACK every THROTTLE_S seconds, and
         none within THROTTLE_S of the worker's own edit of that ACK (the worker's richer status wins).
  stall  run by the main heartbeat every minute (scripts/heartbeat.py): an ACK still open with no tool
         call for STALL_S gets "⚠️ אין תזוזה X דק'" appended (a hook cannot fire without tool calls).
         The next tool call replaces it at once.

Which ACK: the worker's task block (ACK_MSG_ID / TITLE / CHAT_ID) is read from the dispatcher's
Agent / SendMessage call (registered by ACK id) and from the worker's own transcript
(<session transcript dir>/<session_id>/subagents/agent-<agent_id>.jsonl, newest block wins, so a
follow-up's ACK replaces the first one). The dispatcher's own tool calls (no agent_id) never edit.

Done: `bot_tg.py delete` of the ACK marks it done (mark_done), the delete command itself is never
"a step", and an edit answered "message to edit not found" closes it too. Nothing edits a done ACK.

State: <bot home>/tmp/ack_live.json (+ .lock). Every entry point never raises and never blocks a tool
call (the hook is async; network calls have a short timeout).
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, "D:/Kai/scripts")

THROTTLE_S = 60          # one hook edit per ACK per minute, and none this soon after the worker's own edit
STALL_S = 600            # no tool call for 10 min = stall marker
STALL_GIVE_UP_S = 3 * 3600   # a worker dead for 3h: stop touching its ACK (the pin sweep clears it)
ENTRY_TTL_S = 12 * 3600
NET_TIMEOUT = 4
TAIL_BYTES = 512 * 1024
STEP_MAX = 70

SKIP_TOOLS = {"mcp__plugin_telegram_telegram__reply", "mcp__plugin_telegram_telegram__react",
              "mcp__plugin_telegram_telegram__edit_message"}
LABELS = {
    "Read": "קורא קובץ", "Edit": "עורך קובץ", "Write": "כותב קובץ", "MultiEdit": "עורך קובץ",
    "NotebookEdit": "עורך מחברת", "Grep": "מחפש בקוד", "Glob": "מחפש קבצים",
    "WebFetch": "קורא דף באינטרנט", "WebSearch": "מחפש באינטרנט", "Agent": "מפעיל agent",
    "SendMessage": "שולח הודעה ל-agent", "ToolSearch": "טוען כלים", "Skill": "טוען skill",
    "TaskStop": "עוצר משימה", "Monitor": "ממתין לתהליך",
}
PREFIX_LABELS = (("mcp__playwright__", "עובד בדפדפן"), ("mcp__claude-in-chrome__", "עובד בכרום"),
                 ("mcp__claude_ai_Google_Calendar__", "בודק ביומן"), ("mcp__claude_ai_Gmail__", "בודק במייל"),
                 ("mcp__claude_ai_Google_Drive__", "עובד בדרייב"), ("mcp__whatsapp", "עובד בוואטסאפ"),
                 ("mcp__plugin_telegram_telegram__", "טלגרם"))

ACK_RE = re.compile(r"(?m)^\s*ACK_MSG_ID\s*[:=]\s*(\d+)")
# a follow-up the dispatcher writes freely: "ACK for this round: 3550 (I pin it)" (appdev, 15/11/2026)
ACK_LOOSE_RE = re.compile(r"\bACK\b[^\d\n]{0,25}?(\d{3,})")
TITLE_RE = re.compile(r"(?m)^\s*TITLE:\s*(.+?)\s*$")
CHAT_RE = re.compile(r"(?m)^\s*CHAT_ID:\s*(-?\d+)")
DELETE_RE = re.compile(r"bot_tg\.py\s+delete\b.*?--msg\s+(\d+)")


# ---------------------------------------------------------------- state

def home_tmp(bot):
    import kai_bots
    return os.path.join(kai_bots.bot(bot)["home"], "tmp")


def _state_path(tmp_dir):
    return os.path.join(tmp_dir, "ack_live.json")


class _Lock:
    """O_EXCL lock; stale after 3s; gives up after 1.5s (never block)."""
    def __init__(self, tmp_dir):
        self.path, self.fd = _state_path(tmp_dir) + ".lock", None
        os.makedirs(tmp_dir, exist_ok=True)

    def __enter__(self):
        end = time.time() + 1.5
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > 3:
                        os.remove(self.path)
                        continue
                except Exception:
                    pass
                if time.time() > end:
                    return self
                time.sleep(0.02)
            except Exception:
                return self

    def __exit__(self, *a):
        if self.fd is not None:
            try:
                os.close(self.fd)
                os.remove(self.path)
            except Exception:
                pass


def load(tmp_dir):
    try:
        with open(_state_path(tmp_dir), encoding="utf-8") as f:
            st = json.load(f)
        st.setdefault("acks", {})
        st.setdefault("agents", {})
        return st
    except Exception:
        return {"acks": {}, "agents": {}}


def save(tmp_dir, st, now=None):
    now = now or time.time()
    st["acks"] = {k: v for k, v in st["acks"].items() if now - v.get("seen", now) < ENTRY_TTL_S}
    live = set(st["acks"])
    st["agents"] = {k: v for k, v in st["agents"].items() if str(v) in live}
    os.makedirs(tmp_dir, exist_ok=True)
    tmp = _state_path(tmp_dir) + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, _state_path(tmp_dir))


# ---------------------------------------------------------------- parsing

def parse_block(text):
    """(ack, title, chat) from a worker task block; ack None when there is none."""
    text = text or ""
    acks = ACK_RE.findall(text) or ACK_LOOSE_RE.findall(text)[:1]
    if not acks:
        return None, None, None
    t = TITLE_RE.findall(text)
    c = CHAT_RE.findall(text)
    return acks[-1], (t[-1] if t else None), (c[-1] if c else None)


def _user_text(entry):
    """Text of a user turn in a transcript line (not a tool result)."""
    if entry.get("type") != "user":
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(parts) if parts else None
    return None


def agent_transcript(transcript_path, session_id, agent_id):
    if not transcript_path or not agent_id:
        return None
    base = os.path.splitext(transcript_path)[0]
    if session_id and os.path.basename(base) != session_id:
        base = os.path.join(os.path.dirname(base), session_id)
    return os.path.join(base, "subagents", f"agent-{agent_id}.jsonl")


def block_from_transcript(path):
    """The newest worker task block in a subagent transcript: first line + the tail (follow-ups)."""
    if not path or not os.path.exists(path):
        return None, None, None
    lines = []
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            first = f.readline()
            lines.append(first)
            if size > len(first):
                f.seek(max(len(first), size - TAIL_BYTES))
                chunk = f.read()
                if f.tell() - len(chunk) > len(first):
                    chunk = chunk.split(b"\n", 1)[-1]   # drop a partial first line
                lines.extend(chunk.split(b"\n"))
    except Exception:
        return None, None, None
    found = (None, None, None)
    for raw in lines:
        if b"ACK" not in raw:
            continue
        try:
            text = _user_text(json.loads(raw.decode("utf-8", errors="replace")))
        except Exception:
            continue
        ack, title, chat = parse_block(text)
        if ack:
            same = ack == found[0]   # a follow-up with its own ACK does not inherit the old title
            found = (ack, title or (found[1] if same else None), chat or found[2])
    return found


def step_label(tool_name, tool_input):
    tool_input = tool_input or {}
    desc = tool_input.get("description") if tool_name in ("Bash", "PowerShell", "Agent") else None
    if desc:
        step = str(desc)
    elif tool_name in LABELS:
        step = LABELS[tool_name]
        fp = tool_input.get("file_path") or tool_input.get("notebook_path")
        if fp and tool_name in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"):
            step += " " + os.path.basename(str(fp))
    else:
        step = next((lab for pre, lab in PREFIX_LABELS if (tool_name or "").startswith(pre)), None)
        if not step:
            name = (tool_name or "כלי").split("__")[-1]
            step = name
    step = " ".join(step.split())
    return step if len(step) <= STEP_MAX else step[:STEP_MAX - 1] + "…"


def local_hhmm(now=None):
    try:
        import local_tz
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(now or time.time(), tz=timezone.utc)
        return local_tz.now_local(now_utc=dt).strftime("%H:%M")
    except Exception:
        return time.strftime("%H:%M", time.localtime(now or time.time()))


def live_text(title, step, now=None):
    head = f"🔄 {title} — " if title else "🔄 "
    return f"{head}⏳ {step} · עודכן {local_hhmm(now)}"


def stall_text(base, minutes):
    base = re.sub(r"\n⚠️ אין תזוזה.*$", "", base or "🔄 ⏳", flags=re.S)
    return f"{base}\n⚠️ אין תזוזה {minutes} דק'"


# ---------------------------------------------------------------- telegram

def _edit(bot, chat, msg, text):
    """'ok' | 'gone' (deleted / not found) | 'same' | 'error'."""
    import bot_tg
    import urllib.error
    import urllib.parse
    import urllib.request
    try:
        try:
            import net_ipv4  # noqa: F401  IPv6 to api.telegram.org is slow on this PC
        except Exception:
            pass
        url = f"https://api.telegram.org/bot{bot_tg.token(bot)}/editMessageText"
        data = urllib.parse.urlencode({"chat_id": chat, "message_id": msg, "text": text}).encode()
        with urllib.request.urlopen(url, data=data, timeout=NET_TIMEOUT) as r:
            r.read()
        return "ok"
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace").lower()
        except Exception:
            body = ""
        if "not modified" in body:
            return "same"
        if "not found" in body or "message_id_invalid" in body or "can't be edited" in body:
            return "gone"
        return "error"
    except Exception:
        return "error"


def _default_chat(bot):
    try:
        import kai_bots
        return kai_bots.bot(bot).get("group_id")
    except (Exception, SystemExit):   # kai_bots.bot() exits on an unknown bot
        return None


# ---------------------------------------------------------------- core

def register(st, ack, title, chat, now, agent=None):
    a = st["acks"].setdefault(str(ack), {"ack": str(ack), "registered": now, "last_tool": now,
                                         "last_edit": 0, "worker_edit": 0, "done": False})
    if title and not a.get("worker_title"):
        a["title"] = title
    if chat:
        a["chat"] = str(chat)
    elif agent and not a.get("chat"):   # a follow-up block without CHAT_ID: same chat as the agent's last ACK
        prev = st["acks"].get(st["agents"].get(agent) or "")
        if prev and prev.get("chat"):
            a["chat"] = prev["chat"]
    a["seen"] = now
    if agent:
        st["agents"][agent] = str(ack)
    return a


def mark_done(bot, ack, tmp_dir=None, now=None):
    """Called by `bot_tg.py delete`: this ACK is gone, never edit it again. Never raises."""
    try:
        tmp_dir = tmp_dir or home_tmp(bot)
        now = now or time.time()
        with _Lock(tmp_dir):
            st = load(tmp_dir)
            a = st["acks"].get(str(ack))
            if a:
                a["done"] = True
                a["seen"] = now
                save(tmp_dir, st, now)
    except Exception:
        pass


def handle(bot, data, tmp_dir=None, now=None, editor=None, resolver=None):
    """The PreToolUse hook. Returns what it did (for tests / the trace): 'edit', 'skip:<why>', ..."""
    tmp_dir = tmp_dir or home_tmp(bot)
    now = now or time.time()
    editor = editor or _edit
    resolver = resolver or block_from_transcript
    tool = data.get("tool_name") or ""
    tin = data.get("tool_input") or {}
    agent = data.get("agent_id")
    claim = None
    with _Lock(tmp_dir):
        st = load(tmp_dir)
        # the dispatcher (or a worker) hands a task block to an agent: remember its ACK
        if tool in ("Agent", "SendMessage"):
            ack, title, chat = parse_block(tin.get("prompt") or tin.get("message") or "")
            if ack:
                to = tin.get("to") if tool == "SendMessage" else None
                register(st, ack, title, chat, now, agent=to)
        # someone deletes an ACK: done (bot_tg.py delete also calls mark_done itself)
        cmd = str(tin.get("command") or "")
        for m in DELETE_RE.findall(cmd):
            if m in st["acks"]:
                st["acks"][m]["done"] = True
        # the worker's own edit of its ACK: it wins for THROTTLE_S
        if tool == "mcp__plugin_telegram_telegram__edit_message":
            a = st["acks"].get(str(tin.get("message_id")))
            if a:
                a["worker_edit"] = now
                a["text"] = tin.get("text") or a.get("text")
                head = str(tin.get("text") or "")
                if head.startswith("🔄") and " — " in head:   # the worker's own title (a follow-up has none)
                    a["title"] = head.split(" — ", 1)[0][1:].strip() or a.get("title")
                    a["worker_title"] = True
                a["last_tool"] = now
                a.pop("stall_min", None)
        if not agent:
            save(tmp_dir, st, now)
            return "skip:dispatcher"
        ack_id = st["agents"].get(agent)
        a = st["acks"].get(ack_id) if ack_id else None
        # first sight, or once a minute (also after its ACK is done): read the newest task block, because a
        # follow-up (SendMessage) brings a new ACK to the same worker (round 8 of appdev, 15/11/2026)
        stale = a is None or now - a.get("resolved", 0) >= THROTTLE_S
        if stale:
            ack, title, chat = resolver(agent_transcript(data.get("transcript_path"), data.get("session_id"),
                                                         agent))
            if ack:
                a = register(st, ack, title, chat, now, agent=agent)
            if a is not None:
                a["resolved"] = now
        if a is None:
            save(tmp_dir, st, now)
            return "skip:no_ack"
        a["last_tool"] = now
        a["seen"] = now
        was_stalled = a.pop("stall_min", None) is not None
        if a.get("done"):
            why = "done"
        elif tool in SKIP_TOOLS or DELETE_RE.search(cmd) or "general-done" in cmd:
            why = "finishing"
        elif not was_stalled and now - max(a.get("last_edit", 0), a.get("worker_edit", 0)) < THROTTLE_S:
            why = "throttle"
        else:
            why = None
            text = live_text(a.get("title"), step_label(tool, tin), now)
            a["last_edit"] = now
            a["text"] = text
            claim = (a.get("chat") or _default_chat(bot), a["ack"], text)
        save(tmp_dir, st, now)
    if not claim:
        return "skip:" + why
    res = editor(bot, claim[0], claim[1], claim[2])
    if res == "gone":
        mark_done(bot, claim[1], tmp_dir=tmp_dir, now=now)
    return "edit:" + res


def stall_sweep(bot, tmp_dir=None, now=None, editor=None):
    """Heartbeat (every minute): an open ACK with no tool call for STALL_S gets the stall line. Never raises."""
    out = []
    try:
        tmp_dir = tmp_dir or home_tmp(bot)
        if not os.path.exists(_state_path(tmp_dir)):
            return out
        now = now or time.time()
        editor = editor or _edit
        todo = []
        with _Lock(tmp_dir):
            st = load(tmp_dir)
            for a in st["acks"].values():
                idle = now - a.get("last_tool", now)
                if a.get("done") or idle < STALL_S or idle > STALL_GIVE_UP_S:
                    continue
                minutes = int(idle // 60)
                if a.get("stall_min") == minutes:
                    continue
                base = a.get("text") or (f"🔄 {a['title']} — ⏳" if a.get("title") else "🔄 ⏳")
                a["stall_min"] = minutes
                todo.append((a.get("chat") or _default_chat(bot), a["ack"], stall_text(base, minutes)))
            if todo:
                save(tmp_dir, st, now)
        for chat, ack, text in todo:
            res = editor(bot, chat, ack, text)
            out.append((ack, res))
            if res == "gone":
                mark_done(bot, ack, tmp_dir=tmp_dir, now=now)
    except Exception as e:
        out.append(("error", type(e).__name__))
    return out


def _trace(tmp_dir, line):
    try:
        p = os.path.join(tmp_dir, "ack_live_trace.log")
        if os.path.exists(p) and os.path.getsize(p) > 512 * 1024:
            os.replace(p, p + ".1")
        with open(p, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except Exception:
        pass


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    bot = argv[argv.index("--bot") + 1] if "--bot" in argv else None
    if not bot:
        print(__doc__)
        return 0
    if cmd == "hook":
        try:
            data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
            res = handle(bot, data)
            if res.startswith("edit"):
                _trace(home_tmp(bot), f"{res} agent={data.get('agent_id')} tool={data.get('tool_name')}")
        except Exception as e:   # a hook must never break the session
            try:
                _trace(home_tmp(bot), f"hook error {type(e).__name__}: {e}")
            except Exception:
                pass
        return 0
    if cmd == "stall":
        print(stall_sweep(bot))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
