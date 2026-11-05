#!/usr/bin/env python
"""Shared helpers for Kai's domain bots (multi-bot stage 1, 05/11/2026).

Each domain bot runs its own Claude Code session. That session's SessionStart
hook records its claude.exe PID in <home>/claude.pid (`record-pid`), so the
main Kai's infrastructure can tell the sessions apart:
  - kai_watchdog.py must not count a bot session as "main Kai is running";
  - restart_claude.ps1 must not kill a bot session when restarting main Kai.

CLI:
  python kai_bots.py record-pid --bot miluim     (SessionStart hook of the bot)
  python kai_bots.py bot-pids                    (prints live bot claude PIDs, comma separated)
"""
import json
import os
import sys

CONFIG = "D:/Kai/config/bots.json"


def load_config(path: str = CONFIG) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("bots", {})
    except Exception:
        return {}


def bot(name: str, path: str = CONFIG) -> dict:
    cfg = load_config(path).get(name)
    if not cfg:
        raise SystemExit(f"unknown bot: {name}")
    return cfg


def _pid_alive_claude(pid: int) -> bool:
    try:
        import psutil
        p = psutil.Process(pid)
        return p.name().lower().startswith("claude")
    except Exception:
        return False


def bot_claude_pids(config_path: str = CONFIG) -> set:
    """Live claude.exe PIDs that belong to domain bots (never to main Kai)."""
    pids = set()
    for cfg in load_config(config_path).values():
        pid_file = os.path.join(cfg.get("home", ""), "claude.pid")
        try:
            with open(pid_file, encoding="utf-8") as f:
                pid = int(f.read().strip())
        except Exception:
            continue
        if _pid_alive_claude(pid):
            pids.add(pid)
    return pids


def find_parent_claude(start_pid: int = None):
    """Walk up from this process to the claude.exe that runs the hook."""
    import psutil
    p = psutil.Process(start_pid or os.getpid())
    for _ in range(10):
        p = p.parent()
        if p is None:
            return None
        if p.name().lower().startswith("claude"):
            return p.pid
    return None


def main(argv):
    if len(argv) >= 2 and argv[1] == "record-pid":
        name = argv[argv.index("--bot") + 1]
        cfg = bot(name)
        pid = find_parent_claude()
        if pid:
            with open(os.path.join(cfg["home"], "claude.pid"), "w", encoding="utf-8") as f:
                f.write(str(pid))
            print(f"[kai_bots] {name} session claude.exe pid={pid}")
        return 0
    if len(argv) >= 2 and argv[1] == "bot-pids":
        print(",".join(str(p) for p in sorted(bot_claude_pids())))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
