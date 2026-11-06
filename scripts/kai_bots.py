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
  python kai_bots.py clear-auth-cache            (start_claude.bat: drop the telegram needs-auth entry, exit 0 always)
  python kai_bots.py stop-all  [--dry-run]       (night mode 02:00: stop every bot session + its task)
  python kai_bots.py start-all [--dry-run]       (night mode 05:00: clear needs-auth cache, schtasks /run)
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


# --- night mode (05/11/2026): stop / start every bot together with main Kai --------------
# kai_night.ps1 calls `stop-all` before it stops main Kai at 02:00 and `start-all` ~20s after
# it starts main Kai at 05:00. Without this, Stop-Claude kills the bot's claude.exe (it kills
# every claude) and nothing ever brings it back.

NEEDS_AUTH_CACHE = os.path.join(os.path.expanduser("~"), ".claude", "mcp-needs-auth-cache.json")
SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "sessions")
TELEGRAM_MCP_KEY = "plugin:telegram:telegram"


def task_name(name: str, cfg: dict) -> str:
    """Windows task that launches the bot with a clean env (cmd /k bots\\start_bot.bat <name>)."""
    return cfg.get("task") or "KaiBot" + name[:1].upper() + name[1:]


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p or ""))


def clear_needs_auth(path: str = NEEDS_AUTH_CACHE) -> bool:
    """Drop the telegram plugin from CC's needs-auth cache. True if an entry was removed.

    One failed plugin start (e.g. a bad token) lands there, and from then on EVERY session skips
    the telegram plugin silently - /reload-plugins still says "1 plugin MCP server" (05/11/2026).
    The key is global (not per project), so a failed BOT start also hits main Kai's next start.

    Never raises: a missing, locked or unreadable file is left exactly as it is (False). Only the
    telegram key is removed; every other key is kept. Written via a temp file + os.replace so a
    crash mid-write cannot leave a half file behind.
    Callers: start-all (each bot) and clear-auth-cache (start_claude.bat, before every main start).
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    if not isinstance(data, dict) or TELEGRAM_MCP_KEY not in data:
        return False
    data.pop(TELEGRAM_MCP_KEY, None)
    tmp = path + ".kai.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False


def session_cwd(pid: int, sessions_dir: str = SESSIONS_DIR):
    """cwd of a live Claude Code session, from ~/.claude/sessions/<pid>.json (None if unknown)."""
    try:
        with open(os.path.join(sessions_dir, f"{pid}.json"), encoding="utf-8") as f:
            return json.load(f).get("cwd")
    except Exception:
        return None


def bot_session_pid(cfg: dict, sessions_dir: str = SESSIONS_DIR, alive=_pid_alive_claude):
    """The bot's claude.exe PID, only if it is alive AND its session cwd is the bot home.

    The double check is what guarantees a stale claude.pid that Windows reused for main Kai's
    claude.exe is never treated as the bot.
    """
    try:
        with open(os.path.join(cfg.get("home", ""), "claude.pid"), encoding="utf-8") as f:
            pid = int(f.read().strip())
    except Exception:
        return None
    if not alive(pid):
        return None
    if _norm(session_cwd(pid, sessions_dir)) != _norm(cfg.get("home")):
        return None
    return pid


def _run(cmd):
    import subprocess
    return subprocess.run(cmd, capture_output=True, text=True).returncode


def stop_bot(name: str, cfg: dict, dry_run: bool = False, run=_run, sessions_dir: str = SESSIONS_DIR,
             alive=_pid_alive_claude) -> str:
    pid = bot_session_pid(cfg, sessions_dir, alive)
    task = task_name(name, cfg)
    if dry_run:
        return f"{name}: would stop claude pid={pid} + tree, end task {task}"
    killed = []
    if pid:
        import psutil
        try:
            root = psutil.Process(pid)
            procs = root.children(recursive=True) + [root]
        except Exception:
            procs = []
        for p in procs:  # children first: the plugin bun must not outlive the session (#034)
            try:
                p.kill()
                killed.append(p.pid)
            except Exception:
                pass
    rc = run(["schtasks", "/end", "/tn", task])  # closes the cmd /k window of the task
    return f"{name}: stopped pids={killed or 'none'} task_end_rc={rc}"


def start_bot(name: str, cfg: dict, dry_run: bool = False, run=_run, wait_s: int = 90,
              cache_path: str = NEEDS_AUTH_CACHE, sleep=None) -> str:
    import time as _t
    sleep = sleep or _t.sleep
    task = task_name(name, cfg)
    # A bot created by new_bot.py waits for its token (setup_bot.py writes <state_dir>/.env). Without it
    # start_bot.bat exits at once and the 90s wait would only end in a false FAILED (05/11/2026).
    if not os.path.exists(os.path.join(cfg.get("state_dir", ""), ".env")):
        return f"{name}: skipped, no token yet (run bots/setup_bot.py --bot {name})"
    if cfg.get("enabled") is False:
        return f"{name}: skipped, disabled in config/bots.json"
    if dry_run:
        return f"{name}: would clear needs-auth cache, end + run task {task}, wait for bot.pid"
    cleared = clear_needs_auth(cache_path)
    run(["schtasks", "/end", "/tn", task])  # the task is IgnoreNew: a leftover cmd window blocks /run
    pid_file = os.path.join(cfg.get("state_dir", ""), "bot.pid")
    try:
        os.remove(pid_file)
    except Exception:
        pass
    rc = run(["schtasks", "/run", "/tn", task])
    if rc != 0:
        return f"{name}: FAILED schtasks /run {task} rc={rc} (cache_cleared={cleared})"
    for i in range(max(1, wait_s // 3)):
        if os.path.exists(pid_file):
            return f"{name}: up, poller bot.pid present after ~{(i + 1) * 3}s (cache_cleared={cleared})"
        sleep(3)
    return f"{name}: FAILED no bot.pid within {wait_s}s (cache_cleared={cleared})"


def main(argv):
    if len(argv) >= 2 and argv[1] == "clear-auth-cache":
        # start_claude.bat, right before every main Kai launch. Always exit 0: never block a start.
        try:
            print(f"[kai_bots] needs-auth telegram entry cleared={clear_needs_auth()}")
        except Exception as e:  # belt and braces; clear_needs_auth already never raises
            print(f"[kai_bots] clear-auth-cache skipped: {e}")
        return 0
    if len(argv) >= 2 and argv[1] in ("stop-all", "start-all"):
        dry = "--dry-run" in argv
        bots = load_config()
        if not bots:
            print("no bots configured")
            return 0
        bad = 0
        for name, cfg in bots.items():
            line = (stop_bot if argv[1] == "stop-all" else start_bot)(name, cfg, dry_run=dry)
            bad += "FAILED" in line
            print(("dry-run " if dry else "") + line)
        return 1 if bad else 0
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
