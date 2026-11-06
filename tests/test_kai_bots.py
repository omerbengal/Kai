"""Multi-bot stage 1: watchdog exclusion, bot history, group/topic discovery."""
import json
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "bots"))

import kai_bots  # noqa: E402
import kai_watchdog  # noqa: E402
import bot_tg  # noqa: E402
import setup_bot  # noqa: E402


def test_watchdog_ignores_bot_sessions():
    assert kai_watchdog.main_claude_running({100, 200}, {200}) is True
    assert kai_watchdog.main_claude_running({200}, {200}) is False   # only the bot is up = main is DOWN
    assert kai_watchdog.main_claude_running(set(), set()) is False


def test_bot_pids_from_pid_files(tmp_path):
    home = tmp_path / "miluim"
    home.mkdir()
    (home / "claude.pid").write_text(str(os.getpid()))  # python, not claude -> not counted
    cfg = tmp_path / "bots.json"
    cfg.write_text(json.dumps({"bots": {"miluim": {"home": str(home)}}}))
    assert kai_bots.bot_claude_pids(str(cfg)) == set()


def test_parse_channel_with_topic():
    prompt = ('<channel source="telegram" chat_id="-1001234" message_id="55" message_thread_id="7" '
              'user="omer" ts="2026-11-05T10:00:00Z">כמה נרשמו?</channel>')
    c = bot_tg.parse_channel(prompt)
    assert c["chat_id"] == "-1001234" and c["thread_id"] == "7" and c["message_id"] == "55"
    assert c["text"] == "כמה נרשמו?"


def test_history_roundtrip(tmp_path):
    db = str(tmp_path / "k.db")
    bot_tg.hook_save("miluim", "incoming", {"prompt": '<channel chat_id="-1" message_id="5" '
                                                       'message_thread_id="7" ts="2026-11-05T10:00:00Z">היי</channel>'}, db)
    bot_tg.hook_save("miluim", "outgoing", {"tool_input": {"chat_id": "-1", "message_thread_id": "7", "text": "שלום"},
                                            "tool_response": {"content": [{"text": "sent (id: 6)"}]}}, db)
    out = bot_tg.context("miluim", 20, db)
    assert "היי" in out and "שלום" in out and "id=6" in out


def test_find_group_and_topic():
    ups = [
        {"my_chat_member": {"chat": {"id": -100555, "type": "supergroup", "title": "הצוות של קאי", "is_forum": True}}},
        {"message": {"chat": {"id": -100555, "type": "supergroup", "is_forum": True}, "message_thread_id": 9,
                     "is_topic_message": True, "forum_topic_created": {"name": "תחום א"}}},
        {"message": {"chat": {"id": -100555, "type": "supergroup", "is_forum": True}, "message_thread_id": 9,
                     "is_topic_message": True, "text": "בדיקה",
                     "reply_to_message": {"forum_topic_created": {"name": "תחום א"}}}},
    ]
    assert setup_bot.find_group_and_topic(ups, "תחום") == ("-100555", "9", "הצוות של קאי")


def test_setup_refuses_main_token():
    with pytest.raises(SystemExit):
        setup_bot.main(["x", "--bot", "miluim", "--token", "<REDACTED_SECRET>:abc"])


# --- night mode: stop / start the bots with main Kai (05/11/2026) ---------------------------

def _bot_env(tmp_path, session_cwd):
    home = tmp_path / "miluim"
    state = home / "telegram_state"
    state.mkdir(parents=True)
    (home / "claude.pid").write_text("4242")
    (state / ".env").write_text("TELEGRAM_BOT_TOKEN=x")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "4242.json").write_text(json.dumps({"pid": 4242, "cwd": session_cwd}))
    return {"home": str(home), "state_dir": str(state)}, str(sessions)


def test_bot_session_pid_requires_bot_cwd(tmp_path):
    cfg, sessions = _bot_env(tmp_path, str(tmp_path / "miluim").replace("/", "\\"))
    assert kai_bots.bot_session_pid(cfg, sessions, alive=lambda p: True) == 4242
    # a reused PID that now belongs to main Kai (cwd D:\Kai) is never the bot
    cfg2, sessions2 = _bot_env(tmp_path / "x", "D:\Kai")
    assert kai_bots.bot_session_pid(cfg2, sessions2, alive=lambda p: True) is None
    assert kai_bots.bot_session_pid(cfg, sessions, alive=lambda p: False) is None


def test_task_name_default_and_override():
    assert kai_bots.task_name("miluim", {}) == "KaiBotMiluim"
    assert kai_bots.task_name("miluim", {"task": "X"}) == "X"


def test_clear_needs_auth(tmp_path):
    p = tmp_path / "cache.json"
    p.write_text(json.dumps({"plugin:telegram:telegram": {"timestamp": 1}, "other": {"timestamp": 2}}))
    assert kai_bots.clear_needs_auth(str(p)) is True
    assert json.loads(p.read_text()) == {"other": {"timestamp": 2}}
    assert kai_bots.clear_needs_auth(str(p)) is False          # nothing left to clear
    assert kai_bots.clear_needs_auth(str(tmp_path / "none.json")) is False


def test_stop_bot_dry_run_touches_nothing(tmp_path):
    cfg, sessions = _bot_env(tmp_path, "D:\Kai")
    calls = []
    line = kai_bots.stop_bot("miluim", cfg, dry_run=True, run=calls.append, sessions_dir=sessions,
                             alive=lambda p: True)
    assert calls == [] and "pid=None" in line   # main Kai's cwd -> not the bot


def test_stop_bot_without_session_only_ends_task(tmp_path):
    cfg, sessions = _bot_env(tmp_path, "D:\Kai")
    calls = []
    line = kai_bots.stop_bot("miluim", cfg, run=lambda c: calls.append(c) or 0, sessions_dir=sessions,
                             alive=lambda p: True)
    assert calls == [["schtasks", "/end", "/tn", "KaiBotMiluim"]] and "pids=none" in line


def test_start_bot_clears_cache_ends_then_runs_task(tmp_path):
    cfg, _ = _bot_env(tmp_path, "x")
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"plugin:telegram:telegram": {"timestamp": 1}}))
    pid_file = os.path.join(cfg["state_dir"], "bot.pid")
    with open(pid_file, "w") as f:
        f.write("old")
    calls = []

    def run(cmd):
        calls.append(cmd)
        if cmd[1] == "/run":
            assert not os.path.exists(pid_file)       # stale bot.pid removed before the launch
            with open(pid_file, "w") as f:
                f.write("123")
        return 0

    line = kai_bots.start_bot("miluim", cfg, run=run, cache_path=str(cache), sleep=lambda s: None)
    assert [c[1] for c in calls] == ["/end", "/run"]
    assert json.loads(cache.read_text()) == {}
    assert "up" in line and "cache_cleared=True" in line


def test_start_bot_reports_failure_without_bot_pid(tmp_path):
    cfg, _ = _bot_env(tmp_path, "x")
    line = kai_bots.start_bot("miluim", cfg, run=lambda c: 0, wait_s=6, cache_path=str(tmp_path / "c.json"),
                              sleep=lambda s: None)
    assert line.startswith("miluim: FAILED")


def test_start_bot_skips_bot_without_token_or_disabled(tmp_path):
    cfg, _ = _bot_env(tmp_path, "x")
    calls = []
    os.remove(os.path.join(cfg["state_dir"], ".env"))
    line = kai_bots.start_bot("trading", cfg, run=calls.append, cache_path=str(tmp_path / "c.json"))
    assert "skipped, no token" in line and calls == [] and "FAILED" not in line
    with open(os.path.join(cfg["state_dir"], ".env"), "w") as f:
        f.write("TELEGRAM_BOT_TOKEN=x")
    line = kai_bots.start_bot("trading", dict(cfg, enabled=False), run=calls.append)
    assert "disabled" in line and calls == []


# --- clear-auth-cache before every main Kai start (05/11/2026, Omer msg 22378) --------------

def test_clear_needs_auth_keeps_other_keys_and_never_raises(tmp_path):
    p = tmp_path / "cache.json"
    p.write_text(json.dumps({"a": 1, "plugin:telegram:telegram": {"t": 1}, "b": {"x": 2}}))
    assert kai_bots.clear_needs_auth(str(p)) is True
    assert json.loads(p.read_text()) == {"a": 1, "b": {"x": 2}}
    assert not (tmp_path / "cache.json.kai.tmp").exists()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert kai_bots.clear_needs_auth(str(bad)) is False and bad.read_text() == "{not json"  # left alone
    d = tmp_path / "locked"
    d.mkdir()                                                    # open() fails like a locked file
    assert kai_bots.clear_needs_auth(str(d)) is False


def test_clear_needs_auth_write_failure_is_swallowed(tmp_path, monkeypatch):
    p = tmp_path / "cache.json"
    p.write_text(json.dumps({"plugin:telegram:telegram": {}, "k": 1}))
    monkeypatch.setattr(kai_bots.os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("locked")))
    assert kai_bots.clear_needs_auth(str(p)) is False
    assert json.loads(p.read_text()) == {"plugin:telegram:telegram": {}, "k": 1}  # untouched
    assert not (tmp_path / "cache.json.kai.tmp").exists()


def test_clear_auth_cache_cli_always_exits_zero(monkeypatch):
    monkeypatch.setattr(kai_bots, "clear_needs_auth", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    assert kai_bots.main(["kai_bots.py", "clear-auth-cache"]) == 0


def test_start_claude_bat_clears_cache_before_launch():
    bat = open(os.path.join(ROOT, "start_claude.bat"), encoding="utf-8", errors="replace").read()
    clear = bat.index("kai_bots.py clear-auth-cache")
    launch = bat.index('claude.exe" --model')
    assert clear < launch
