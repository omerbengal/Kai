#!/usr/bin/env python
"""Release gate for a code-owning Kai bot (appdev, 14/11/2026, Omer msg 23264).

Omer's rule: the bot works in a worktree, runs analyze + tests, lets him review, and merges to main (and deploys
Functions) ONLY after his button. That button is checked in CODE, not by the model:

  hook   --bot X   UserPromptSubmit hook. A callback `relok:<slug>` pressed by Omer in this bot's group (or his
                   DM) writes <home>/tmp/release_ok.json {slug, approved_at, source}; `relno:<slug>` removes it.
                   Prints a <system-reminder> line the model sees (RELEASE_APPROVAL ...).
  status --bot X   prints the current approval and whether it is still fresh.

bots/guard.py blocks every command matching config/bots.json `release_gate.patterns` (git push, firebase
deploy, gh pr merge) unless an approval is fresher than `release_gate.window_min` minutes. The model cannot write
the approval itself: the guard blocks any tool call that names release_ok.json or runs this hook.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "D:/Kai/scripts")
sys.path.insert(0, "D:/Kai/bots")
import kai_bots  # noqa: E402

OMER_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
CALLBACK_RE = re.compile(r"^rel(ok|no):([A-Za-z0-9_\-]{1,40})$")


def approval_path(cfg):
    return os.path.join(cfg["home"], "tmp", "release_ok.json")


def read_approval(cfg):
    try:
        with open(approval_path(cfg), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def fresh(cfg, now=None):
    """The approval dict when it is younger than window_min, else None."""
    gate = cfg.get("release_gate") or {}
    a = read_approval(cfg)
    if not a:
        return None
    try:
        at = datetime.fromisoformat(a["approved_at"])
    except Exception:
        return None
    now = now or datetime.now(timezone.utc)
    if now - at > timedelta(minutes=int(gate.get("window_min", 30))):
        return None
    return a


PROTECTED = {"main", "master", "head", "refs/heads/main", "refs/heads/master", "gh-pages", "refs/heads/gh-pages"}
PUSH_RE = re.compile(r"\bgit\b(?:\s+-[cC]\s+\S+|\s+--?[\w-]+(?:=\S+)?)*\s+push\b([^;&|\n]*)")


def push_needs_gate(cmd):
    """True when a shell command holds a `git push` that is not an explicit push of non-protected branches.

    Allowed without approval: `git push [-u] origin feat/x [other-branch ...]` (feature branches, so Omer can
    review them from his Mac). Gated: a bare `git push` / `git push origin` (pushes whatever is current), any
    refspec whose destination is main / master / HEAD / gh-pages, --all / --mirror, and anything unparsable."""
    for m in PUSH_RE.finditer(cmd or ""):
        args = m.group(1).split()
        if any(a in ("--all", "--mirror") or a.startswith("--all=") for a in args):
            return True
        pos = [a.strip("'\"") for a in args if not a.startswith("-")]
        if len(pos) < 2:
            return True
        for spec in pos[1:]:
            spec = spec.lstrip("+")
            if spec.startswith(":"):  # delete a remote branch
                dst = spec[1:]
            else:
                dst = spec.split(":", 1)[1] if ":" in spec else spec
            if not dst or dst.lower() in PROTECTED or "$" in dst or "`" in dst:
                return True
    return False


def hook(bot, prompt, cfg=None, now=None):
    import auto_ack as main_ack  # D:/Kai/scripts
    cfg = cfg or kai_bots.bot(bot)
    lines = []
    for attrs, _content in main_ack.parse_channels(prompt):
        m = CALLBACK_RE.match(attrs.get("callback_data", ""))
        if not m:
            continue
        if attrs.get("user_id") != OMER_ID or attrs.get("chat_id") not in {str(cfg.get("group_id")), OMER_ID}:
            lines.append(f"RELEASE_APPROVAL {m.group(2)} refused (not Omer / not this bot's chat)")
            continue
        path = approval_path(cfg)
        if m.group(1) == "ok":
            os.makedirs(os.path.dirname(path), exist_ok=True)
            rec = {"slug": m.group(2), "approved_at": (now or datetime.now(timezone.utc)).isoformat(),
                   "source": f"tg_callback:{attrs.get('chat_id')}:{attrs.get('message_id')}"}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rec, f)
            win = (cfg.get("release_gate") or {}).get("window_min", 30)
            lines.append(f"RELEASE_APPROVAL {m.group(2)} recorded by Omer's button: merge to main / push / deploy "
                         f"are open for {win} minutes")
        else:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            lines.append(f"RELEASE_APPROVAL {m.group(2)} declined by Omer: nothing goes to main")
    return lines


def main(argv):
    bot = argv[argv.index("--bot") + 1] if "--bot" in argv else None
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "hook":
        try:
            data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
            lines = hook(bot, data.get("prompt", "") or data.get("content", ""))
        except Exception as e:
            lines = [f"RELEASE_APPROVAL hook error {type(e).__name__}"]
        if lines:
            sys.stdout.buffer.write(("<system-reminder>\n" + "\n".join(lines) + "\n</system-reminder>\n")
                                    .encode("utf-8"))
        return 0
    if cmd == "status":
        cfg = kai_bots.bot(bot)
        a = read_approval(cfg)
        f = fresh(cfg)
        print(json.dumps({"approval": a, "fresh": bool(f)}, ensure_ascii=False))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
