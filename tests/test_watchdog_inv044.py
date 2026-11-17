"""Investigation 044 (17/11/2026): the watchdog retried a crashing restart 24 times, every 2 minutes, and
opened console windows on every run. Now: no windows, "unknown" never restarts, a cap of 3 attempts with
one alert + retry button, and a parser for the last restart.log error."""
import ast
import json
import sys

sys.path.insert(0, "D:/Kai/scripts")
import kai_watchdog as W  # noqa: E402
import mem_guard  # noqa: E402


def setup(monkeypatch, tmp_path, state=False, retry=False):
    for name in ("DOWN_FILE", "LOCK_FILE", "ATTEMPTS_FILE", "GAVE_UP_FILE", "RETRY_HANDLED_FILE"):
        monkeypatch.setattr(W, name, str(tmp_path / name.lower()))
    monkeypatch.setattr(W.night_mode, "should_stand_down", lambda *_a, **_k: False)
    for fn in ("check_reaction_channel", "check_telegram_patch", "check_low_memory"):
        monkeypatch.setattr(W, fn, lambda: None)
    monkeypatch.setattr(W, "claude_state", lambda: state)
    monkeypatch.setattr(W, "retry_requested", lambda: retry)
    monkeypatch.setattr(W, "last_restart_error", lambda: "RESTART RUN CRASHED: boom")
    monkeypatch.setattr(W, "memory_line", lambda: "mem")
    ev = {"restarts": 0, "logs": [], "texts": [], "buttons": []}
    monkeypatch.setattr(W, "restart_kai", lambda: ev.__setitem__("restarts", ev["restarts"] + 1) or "schtasks")
    monkeypatch.setattr(W, "log_to_db", lambda a, d: ev["logs"].append(a))
    monkeypatch.setattr(W, "send_telegram", lambda t: ev["texts"].append(t))
    monkeypatch.setattr(W, "send_telegram_buttons", lambda t, k: ev["buttons"].append((t, k)))
    return ev


def test_every_subprocess_call_has_no_window():
    tree = ast.parse(open("D:/Kai/scripts/kai_watchdog.py", encoding="utf-8").read())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr in ("run", "Popen", "call", "check_output")
             and getattr(n.func.value, "id", "") == "subprocess"]
    assert calls
    for c in calls:
        assert any(k.arg == "creationflags" for k in c.keywords), f"line {c.lineno} opens a window"


def test_no_powershell_children_left():
    src = open("D:/Kai/scripts/kai_watchdog.py", encoding="utf-8").read()
    assert src.count('["powershell"') == 1  # only the hidden (CREATE_NO_WINDOW) breakaway restart fallback
    assert "\"Get-CimInstance Win32_Process" not in src and "(Get-Process -Name claude" not in src


def test_unknown_state_never_restarts(monkeypatch, tmp_path):
    ev = setup(monkeypatch, tmp_path, state=None)
    W.main()
    assert ev["restarts"] == 0 and "check_unknown" in ev["logs"]


def test_claude_state_is_none_when_process_list_fails(monkeypatch):
    import psutil

    def boom(*_a, **_k):
        raise RuntimeError("starving")
    monkeypatch.setattr(psutil, "process_iter", boom)
    assert W.claude_state() is None
    assert W.is_claude_running() is False


def test_cap_three_attempts_then_one_alert_with_button(monkeypatch, tmp_path):
    ev = setup(monkeypatch, tmp_path, state=False)
    for _ in range(6):
        W.main()
        W._remove(W.LOCK_FILE)  # the wrapper clears it when a run ends
    assert ev["restarts"] == 3
    assert len(ev["buttons"]) == 1
    text, kb = ev["buttons"][0]
    assert "RESTART RUN CRASHED: boom" in text and "mem" in text
    assert kb[0][0]["callback_data"] == W.RETRY_CALLBACK
    assert ev["logs"].count("restart_gave_up") == 1


def test_retry_button_starts_a_new_round(monkeypatch, tmp_path):
    ev = setup(monkeypatch, tmp_path, state=False, retry=True)
    W._write_int(W.ATTEMPTS_FILE, 3)
    W._write_int(W.GAVE_UP_FILE, 1)
    W.main()
    assert ev["restarts"] == 1
    assert W._read_int(W.ATTEMPTS_FILE) == 1
    assert "restart_retry_button" in ev["logs"]


def test_recovery_clears_the_round(monkeypatch, tmp_path):
    ev = setup(monkeypatch, tmp_path, state=True)
    W._write_int(W.DOWN_FILE, 5)
    W._write_int(W.ATTEMPTS_FILE, 3)
    W._write_int(W.GAVE_UP_FILE, 1)
    W.main()
    assert W._read_int(W.ATTEMPTS_FILE) == 0 and W._read_int(W.GAVE_UP_FILE) == 0
    assert "restart_success" in ev["logs"]


def test_find_retry_press_filters_user_data_and_handled():
    ups = [
        {"callback_query": {"id": "a", "data": "other", "from": {"id": ${KAI_TELEGRAM_CHAT_ID}}}},
        {"callback_query": {"id": "b", "data": W.RETRY_CALLBACK, "from": {"id": 1}}},
        {"callback_query": {"id": "c", "data": W.RETRY_CALLBACK, "from": {"id": ${KAI_TELEGRAM_CHAT_ID}}}},
        {"callback_query": {"id": "d", "data": W.RETRY_CALLBACK, "from": {"id": ${KAI_TELEGRAM_CHAT_ID}}}},
        {"message": {"text": "hi"}},
    ]
    assert W.find_retry_press(ups, [])["id"] == "c"
    assert W.find_retry_press(ups, ["c"])["id"] == "d"
    assert W.find_retry_press(ups, ["c", "d"]) is None


def test_last_restart_error_reads_mixed_encoding_log(tmp_path):
    log = tmp_path / "restart.log"
    old = "\n===== RESTART RUN 2026-11-17 20:56:24 (pid 1) =====\nERROR: old one\n"
    utf16 = "Writing session handoff...\r\n".encode("utf-16-le")
    new = "\n===== RESTART RUN 2026-11-17 20:58:11 (pid 2) =====\n"
    tail = "===== RESTART RUN CRASHED: You cannot call a method on a null-valued expression. =====\n"
    log.write_bytes(old.encode() + new.encode() + utf16 + tail.encode())
    assert W.last_restart_error(str(log)) == ("RESTART RUN CRASHED: You cannot call a method on a "
                                              "null-valued expression.")
    assert W.last_restart_error(str(tmp_path / "missing.log")) == "restart.log לא נקרא"


def test_mem_guard_threshold_and_unknown(monkeypatch):
    gb = mem_guard.GB
    assert mem_guard.enough(6, {"free": 7 * gb, "limit": 32 * gb, "phys_free": 0, "phys_total": 0})
    assert not mem_guard.enough(6, {"free": 2 * gb, "limit": 32 * gb, "phys_free": 0, "phys_total": 0})
    monkeypatch.setattr(mem_guard, "commit_status", lambda: None)
    assert mem_guard.enough(6) is True  # unknown never blocks
    assert mem_guard.main(["x"]) == 0


def test_mem_guard_reads_real_numbers():
    st = mem_guard.commit_status()
    assert st and st["limit"] > 0 and 0 <= st["free"] <= st["limit"]
    assert json.dumps(mem_guard.top_memory(2))
