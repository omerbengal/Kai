"""bots/bot_typing.py - per-bot typing lease (05/11/2026, Omer msg 22485)."""
import json
import os
import sys
import time

sys.path.insert(0, "D:/Kai/bots")
sys.path.insert(0, "D:/Kai/scripts")
import bot_typing as bt  # noqa: E402
import new_bot  # noqa: E402

GROUP = "-1004297864522"


def P(tmp_path):
    return bt.Paths("miluim", home=str(tmp_path))


def fake_spawn(p):
    return os.getpid()   # "alive", so no real loop is started


def test_state_is_per_bot_not_main(tmp_path):
    p = bt.Paths("miluim")
    assert p.lease.replace("\\", "/").startswith("D:/KaiBots/miluim/")
    assert "D:/Kai/tmp" not in p.lease.replace("\\", "/")
    assert bt.Paths("trading").lease != p.lease


def test_open_keeps_thread(tmp_path):
    p = P(tmp_path)
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn)
    lease = json.load(open(p.lease))
    assert lease["chat_id"] == GROUP and lease["thread_id"] == "2" and lease["turn_open"]
    assert lease["deadline"] > time.time() + 10


def test_renew_never_opens(tmp_path):
    p = P(tmp_path)
    assert bt.renew(p, spawn=fake_spawn) is False
    assert not os.path.exists(p.lease)


def test_pause_then_grace_then_resume(tmp_path):
    p = P(tmp_path)
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn)
    bt.pause(p)
    assert json.load(open(p.lease))["deadline"] == 0
    assert bt.renew(p, spawn=fake_spawn) is False          # trailing cleanup cannot revive it
    lease = json.load(open(p.lease))
    lease["paused_at"] = time.time() - bt.RESUME_GRACE - 1
    json.dump(lease, open(p.lease, "w"))
    assert bt.renew(p, spawn=fake_spawn) is True           # later work does
    assert json.load(open(p.lease))["deadline"] > time.time()


def test_agent_hold_is_longer_and_not_shortened(tmp_path):
    p = P(tmp_path)
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn)
    bt.renew(p, hold=60, spawn=fake_spawn)
    bt.renew(p, spawn=fake_spawn)                           # the generic * hook must not cut it to 15s
    assert json.load(open(p.lease))["deadline"] > time.time() + 50


def test_loop_sends_to_topic_and_stops_on_expiry(tmp_path):
    p = P(tmp_path)
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn)
    sent = []
    clock = {"n": 0}

    def sleeper(_):
        clock["n"] += 1
        if clock["n"] == 6:                                 # ~1.5 send cycles, then the lease is paused
            bt.pause(p)
    bt.loop(p, sender=lambda pp, c, t: sent.append((c, t)), sleep=sleeper)
    assert sent and all(x == (GROUP, "2") for x in sent)
    assert not os.path.exists(p.inflight)


def test_parse_open_filters():
    msg = '<channel source="plugin:telegram:telegram" chat_id="%s" message_id="5" message_thread_id="2">hi</channel>'
    assert bt.parse_open(msg % GROUP, GROUP) == (GROUP, "2", "5")
    assert bt.parse_open(msg % "-100999", GROUP) is None
    react = '<channel source="plugin:telegram:telegram" chat_id="%s" event_type="reaction">x</channel>' % GROUP
    assert bt.parse_open(react, GROUP) is None
    assert bt.parse_open("no channel", GROUP) is None


def test_generated_settings_have_typing_hooks():
    s = new_bot.settings("whatsapp", ["whatsapp"])["hooks"]
    flat = json.dumps(s)
    assert "bot_typing.py open --bot whatsapp" in flat
    assert "bot_typing.py pause --bot whatsapp" in flat
    assert "bot_typing.py renew --hold 60 --bot whatsapp" in flat
    assert "end-turn --bot whatsapp" in flat
    assert "typing_session" not in flat and "typing_lease" not in flat   # never main Kai's scripts
    pre = s["PreToolUse"]
    reply = [h for h in pre if h["matcher"] == "mcp__plugin_telegram_telegram__reply"][0]["hooks"][0]
    assert not reply.get("async")                                          # pause must be synchronous
    assert "guard.py" in pre[0]["hooks"][0]["command"]                       # guard still first


# ---- parallel tasks (Omer msg 22497) ------------------------------------------------------------

def _lease(p):
    return json.load(open(p.lease))


def test_two_overlapping_tasks_reply1_keeps_typing_reply2_stops(tmp_path):
    p = P(tmp_path)
    bt.open_lease(p, GROUP, "117", spawn=fake_spawn, msg_id="10")
    bt.attach_ack(p, "10", "11")
    bt.open_lease(p, GROUP, "117", spawn=fake_spawn, msg_id="12")
    bt.attach_ack(p, "12", "13")
    assert set(_lease(p)["tasks"]) == {"10", "12"}
    # task 1: final reply (pause) + its ACK delete
    bt.pause(p, reply_to="10")
    assert bt.end_task(p, ack_id="11", spawn=fake_spawn) == 1
    lease = _lease(p)
    assert lease["paused_at"] == 0 and lease["deadline"] > time.time() + 10      # typing back at once
    assert bt._lease_alive(p, os.getpid()) is not None
    # task 2: final reply + ACK delete -> stops for good
    bt.pause(p, reply_to="12")
    assert bt.end_task(p, ack_id="13", spawn=fake_spawn) == 0
    lease = _lease(p)
    assert lease["deadline"] == 0 and lease["paused_at"] > 0
    assert bt._lease_alive(p, os.getpid()) is None
    assert bt.renew(p, spawn=fake_spawn) is False                                 # trailing calls stay off


def test_worker2_tool_call_after_short_grace_revives(tmp_path):
    p = P(tmp_path)
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn, msg_id="1")
    bt.attach_ack(p, "1", "2")
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn, msg_id="3")
    bt.attach_ack(p, "3", "4")
    bt.pause(p, reply_to="1")             # task 1 replied, but its ACK delete has not happened yet
    lease = _lease(p)
    lease["paused_at"] = time.time() - bt.PAUSE_RESUME - 0.5
    json.dump(lease, open(p.lease, "w"))
    assert bt.renew(p, spawn=fake_spawn) is True


def test_open_tasks_keep_typing_through_a_long_tool_call(tmp_path):
    p = P(tmp_path)
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn, msg_id="1")
    bt.attach_ack(p, "1", "2")
    lease = _lease(p)
    lease["deadline"] = time.time() - 1                    # 15s lease ran out mid Bash call
    json.dump(lease, open(p.lease, "w"))
    assert bt._lease_alive(p, os.getpid()) is not None
    lease["last_activity"] = time.time() - bt.IDLE_CAP - 1
    json.dump(lease, open(p.lease, "w"))
    assert bt._lease_alive(p, os.getpid()) is None         # bounded: no tool call for IDLE_CAP (dead worker) = off


def test_ackless_task_closes_on_reply_and_does_not_resume(tmp_path):
    p = P(tmp_path)
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn, msg_id="5")      # trivial: no ACK
    bt.pause(p, reply_to="5")
    assert _lease(p)["tasks"] == {}
    assert bt._lease_alive(p, os.getpid()) is None


def test_stale_ackless_task_does_not_revive_after_other_task_ends(tmp_path):
    p = P(tmp_path)
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn, msg_id="5")      # trivial, answered by a react only
    lease = _lease(p)
    lease["tasks"]["5"]["opened"] = time.time() - bt.NOACK_TTL - 1
    json.dump(lease, open(p.lease, "w"))
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn, msg_id="7")
    bt.attach_ack(p, "7", "8")
    bt.pause(p, reply_to="7")
    assert bt.end_task(p, ack_id="8", spawn=fake_spawn) == 0
    assert bt._lease_alive(p, os.getpid()) is None


def test_bot_tg_delete_ends_the_task(tmp_path, monkeypatch):
    import bot_tg
    p = P(tmp_path)
    monkeypatch.setattr(bt, "Paths", lambda bot, home=None: p)
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn, msg_id="1")
    bot_tg._typing("attach_ack", "miluim", "1", "99")
    assert _lease(p)["tasks"]["1"]["ack"] == "99"
    monkeypatch.setattr(bt, "_spawn_loop", fake_spawn)
    bot_tg._typing("end_task", "miluim", None, "99")
    assert _lease(p)["tasks"] == {}


def test_ack_before_open_is_kept_and_bot_without_hooks_never_types(tmp_path):
    p = P(tmp_path)
    bt.attach_ack(p, "1", "2")                         # auto-ACK finished before the async open hook
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn, msg_id="1")
    assert _lease(p)["tasks"]["1"]["ack"] == "2"
    q = bt.Paths("miluim", home=str(tmp_path / "nohooks"))
    bt.attach_ack(q, "1", "2")
    bt.attach_ack(q, "3", "4")
    spawned = []
    bt.end_task(q, ack_id="2", spawn=lambda pp: spawned.append(1) or 0)
    assert not spawned and bt._lease_alive(q, os.getpid()) is None


def test_manual_ack_reply_keeps_the_task_and_its_delete_ends_it(tmp_path, monkeypatch):
    """15/11/2026 (appdev tag 3346): auto-ACK failed, the bot sent '🔄 ...' itself by reply - typing
    must last while the worker runs, and the ACK's delete ends the task."""
    import bot_tg
    p = P(tmp_path)
    bt.open_lease(p, GROUP, "2876", spawn=fake_spawn, msg_id="3313")
    bt.pause(p, reply_to="3313", text="\u200f🔄 סבב 6 — ⏳ מתחיל...")
    assert _lease(p)["tasks"]["3313"]["ack"] == "pending"
    monkeypatch.setattr(bt, "Paths", lambda bot, home=None: p)
    monkeypatch.setattr(bot_tg, "_buttons", lambda *a, **k: None)
    monkeypatch.setattr(bot_tg, "save", lambda *a, **k: None)
    monkeypatch.setattr(bot_tg, "settle_on_general_reply", lambda *a, **k: None)
    bot_tg.hook_save("miluim", "outgoing", {
        "tool_input": {"chat_id": GROUP, "message_thread_id": "2876", "reply_to": "3313",
                       "text": "🔄 סבב 6 — ⏳ מתחיל..."},
        "tool_response": {"content": [{"type": "text", "text": "sent (id: 3314)"}]}}, db_path=None)
    assert _lease(p)["tasks"]["3313"]["ack"] == "3314"
    lease = _lease(p)                                   # a long tool call: open task keeps typing
    lease.update(deadline=0, paused_at=0, last_activity=time.time() - 5, pid=os.getpid())
    json.dump(lease, open(p.lease, "w"))
    assert bt._lease_alive(p, os.getpid())
    assert bt.end_task(p, ack_id="3314", spawn=fake_spawn) == 0


def test_manual_ack_never_replaces_a_real_ack_and_plain_reply_still_closes(tmp_path):
    p = P(tmp_path)
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn, msg_id="1")
    bt.attach_ack(p, "1", "2")
    assert bt.attach_manual_ack(p, "1", "9") is False
    assert _lease(p)["tasks"]["1"]["ack"] == "2"
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn, msg_id="5")
    bt.pause(p, reply_to="5", text="בוקר טוב")
    assert "5" not in _lease(p)["tasks"]
    assert bt.is_ack_text("🎤 מתמלל... ⏳") and not bt.is_ack_text("✅ done")


def test_attach_cli(tmp_path, monkeypatch):
    p = P(tmp_path)
    monkeypatch.setattr(bt, "Paths", lambda bot, home=None: p)
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn, msg_id="1")
    bt.main(["bot_typing.py", "attach", "--bot", "miluim", "--msg", "1", "--ack", "7"])
    assert _lease(p)["tasks"]["1"]["ack"] == "7"


# ---- background work (15/11/2026, Omer msg 3688): a worker waiting on a run_in_background process

def _bg_setup(tmp_path):
    p = P(tmp_path)
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn, msg_id="1")
    bt.attach_ack(p, "1", "2")
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn, msg_id="3")
    bt.attach_ack(p, "3", "4")
    json.dump({"acks": {"4": {"ack": "4", "seen": time.time()}}, "agents": {"agentB": "4"}},
              open(os.path.join(p.dir, "ack_live.json"), "w", encoding="utf-8"))
    return p


def _age(p, **kw):
    lease = _lease(p)
    lease.update(kw)
    json.dump(lease, open(p.lease, "w"))


def test_is_bg_call():
    assert bt.is_bg_call("Bash", {"command": "x", "run_in_background": True})
    assert bt.is_bg_call("PowerShell", {"run_in_background": True})
    assert bt.is_bg_call("Monitor", {})
    assert not bt.is_bg_call("Bash", {"command": "x"})
    assert not bt.is_bg_call("Read", {"run_in_background": True})


def test_bg_marks_only_the_callers_task_via_ack_live_state(tmp_path):
    p = _bg_setup(tmp_path)
    assert bt.mark_bg(p, agent_id="agentB", spawn=fake_spawn) == ["3"]
    tasks = _lease(p)["tasks"]
    assert tasks["3"].get("bg") and not tasks["1"].get("bg")


def test_bg_unknown_agent_marks_every_acked_task(tmp_path):
    p = _bg_setup(tmp_path)
    assert sorted(bt.mark_bg(p, agent_id="nobody", spawn=fake_spawn)) == ["1", "3"]


def test_bg_keeps_typing_without_tool_calls_until_ack_deleted(tmp_path):
    p = _bg_setup(tmp_path)
    bt.mark_bg(p, agent_id="agentB", spawn=fake_spawn)
    _age(p, deadline=time.time() - 1, last_activity=time.time() - bt.IDLE_CAP - 300)
    assert bt._lease_alive(p, os.getpid()) is not None       # worker's turn ended, render still running
    bt.end_task(p, ack_id="2", spawn=fake_spawn)              # the other task ends: still typing
    _age(p, deadline=time.time() - 1, last_activity=time.time() - bt.IDLE_CAP - 300)
    assert bt._lease_alive(p, os.getpid()) is not None
    bt.end_task(p, ack_id="4", spawn=fake_spawn)              # its ACK deleted: off
    _age(p, deadline=time.time() - 1)
    assert bt._lease_alive(p, os.getpid()) is None


def test_bg_hard_cap(tmp_path):
    p = _bg_setup(tmp_path)
    bt.end_task(p, ack_id="2", spawn=fake_spawn)
    bt.mark_bg(p, agent_id="agentB", spawn=fake_spawn)
    lease = _lease(p)
    lease["tasks"]["3"]["bg"] = time.time() - bt.BG_HOLD - 1
    lease["deadline"] = time.time() - 1
    lease["last_activity"] = time.time() - bt.IDLE_CAP - 1
    json.dump(lease, open(p.lease, "w"))
    assert bt._lease_alive(p, os.getpid()) is None


def test_bg_extends_an_old_task_past_task_ttl(tmp_path):
    p = _bg_setup(tmp_path)
    lease = _lease(p)
    lease["tasks"]["3"]["opened"] = time.time() - bt.TASK_TTL - 60   # long video task, render launched now
    json.dump(lease, open(p.lease, "w"))
    assert bt.mark_bg(p, agent_id="agentB", spawn=fake_spawn) == ["3"]
    _age(p, deadline=time.time() - 1, last_activity=time.time() - bt.IDLE_CAP - 1)
    assert bt._lease_alive(p, os.getpid()) is not None


def test_bg_loop_waits_through_a_progress_reply_then_resumes(tmp_path):
    p = _bg_setup(tmp_path)
    bt.end_task(p, ack_id="2", spawn=fake_spawn)
    bt.mark_bg(p, agent_id="agentB", spawn=fake_spawn)
    bt.pause(p)                                               # "rendering, will send soon" reply
    assert bt._loop_state(p, os.getpid())[0] == "wait"        # loop stays, sends nothing
    _age(p, paused_at=time.time() - bt.PAUSE_RESUME - 1)
    assert bt._loop_state(p, os.getpid())[0] == "send"


def test_renew_hook_marks_bg(tmp_path, monkeypatch):
    p = _bg_setup(tmp_path)
    monkeypatch.setattr(bt, "Paths", lambda bot, home=None: p)
    monkeypatch.setattr(bt, "_spawn_loop", fake_spawn)
    monkeypatch.setattr(bt, "renew", lambda *a, **k: True)
    import io
    data = {"tool_name": "Bash", "tool_input": {"command": "render", "run_in_background": True},
            "agent_id": "agentB"}
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(data).encode())))
    bt.main(["bot_typing.py", "renew", "--bot", "miluim"])
    assert _lease(p)["tasks"]["3"].get("bg")


def test_main_kai_general_message_does_not_open():
    ack = ('<channel source="plugin:telegram:telegram" chat_id="%s" message_id="9" user_id="%s">🔄 ⏳</channel>'
           % (GROUP, bt.MAIN_BOT_ID))
    assert bt.parse_open(ack, GROUP, own_topic="2", own_username="some_bot") is None
    tag = ack.replace("🔄 ⏳", "@some_bot please do X")
    assert bt.parse_open(tag, GROUP, own_topic="2", own_username="some_bot") == (GROUP, "2", "9")


# ---- open foreground tool calls (15/11/2026, Omer msg 3704): a long flutter build in the foreground

def _one_task(tmp_path):
    p = P(tmp_path)
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn, msg_id="1")
    bt.attach_ack(p, "1", "2")
    return p


def test_call_cap_uses_the_calls_timeout():
    assert bt.call_cap("Bash", {"timeout": 300000}) == 300 + bt.CALL_MARGIN
    assert bt.call_cap("Bash", {}) == bt.CALL_DEFAULT + bt.CALL_MARGIN
    assert bt.call_cap("PowerShell", {"timeout": 9999999}) == bt.CALL_MAX + bt.CALL_MARGIN
    assert bt.call_cap("Read", {"timeout": 300000}) == bt.CALL_DEFAULT + bt.CALL_MARGIN


def test_open_call_keeps_typing_until_post(tmp_path):
    p = _one_task(tmp_path)
    assert bt.open_call(p, "tu1", "Bash", {"command": "build", "timeout": 600000})
    _age(p, deadline=time.time() - 1, last_activity=time.time() - 400)   # minutes into the build
    assert bt._lease_alive(p, os.getpid()) is not None
    bt.close_call(p, "tu1")
    _age(p, deadline=time.time() - 1, last_activity=time.time() - bt.IDLE_CAP - 1)
    assert bt._lease_alive(p, os.getpid()) is None


def test_open_call_capped_at_its_timeout(tmp_path):
    p = _one_task(tmp_path)
    bt.open_call(p, "tu1", "Bash", {"command": "build"})
    lease = _lease(p)
    lease["calls"]["tu1"]["until"] = time.time() - 1                    # no Post ever came (interrupted)
    lease["deadline"] = time.time() - 1
    lease["last_activity"] = time.time() - bt.IDLE_CAP - 1
    json.dump(lease, open(p.lease, "w"))
    assert bt._lease_alive(p, os.getpid()) is None


def test_open_call_needs_an_open_task_and_never_opens(tmp_path):
    p = P(tmp_path)
    assert bt.open_call(p, "tu1", "Bash", {}) is False                  # no lease: nothing
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn)                      # lease, but no task
    bt.open_call(p, "tu1", "Bash", {})
    _age(p, deadline=time.time() - 1)
    assert bt._lease_alive(p, os.getpid()) is None


def test_post_before_pre_and_skipped_calls(tmp_path):
    p = _one_task(tmp_path)
    bt.close_call(p, "tu9")                                             # async Post landed first
    assert bt.open_call(p, "tu9", "Bash", {}) is False
    assert bt.open_call(p, "tu2", "Bash", {"run_in_background": True}) is False
    assert bt.open_call(p, "tu3", "mcp__plugin_telegram_telegram__reply", {}) is False
    assert bt.open_call(p, None, "Bash", {}) is False


def test_post_hook_cli_closes(tmp_path, monkeypatch):
    p = _one_task(tmp_path)
    monkeypatch.setattr(bt, "Paths", lambda bot, home=None: p)
    bt.open_call(p, "tu1", "Bash", {})
    import io
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps({"tool_use_id": "tu1"}).encode())))
    bt.main(["bot_typing.py", "post", "--bot", "miluim"])
    assert "tu1" not in _lease(p).get("calls", {})


def test_generated_settings_have_post_hooks():
    h = new_bot.settings("whatsapp", ["whatsapp"])["hooks"]
    for ev in ("PostToolUse", "PostToolUseFailure"):
        cmds = [x["command"] for e in h[ev] if e["matcher"] == "*" for x in e["hooks"]]
        assert any("bot_typing.py post --bot whatsapp" in c for c in cmds)


# ---- a task with an ACK types until its ACK is deleted (15/11/2026, Omer msg 3744)

def test_live_task_types_through_a_long_think_without_tool_calls(tmp_path):
    p = _one_task(tmp_path)
    _age(p, deadline=time.time() - 1, last_activity=time.time() - 90)       # model writing a long file
    assert bt._lease_alive(p, os.getpid()) is not None


def test_ackless_task_still_stops_after_the_activity_window(tmp_path):
    p = P(tmp_path)
    bt.open_lease(p, GROUP, "2", spawn=fake_spawn, msg_id="1")              # trivial message, no ACK
    _age(p, deadline=time.time() - 1, last_activity=time.time() - bt.ACTIVITY_WINDOW - 1)
    assert bt._lease_alive(p, os.getpid()) is None


def test_progress_reply_waits_then_resumes_by_itself(tmp_path):
    p = _one_task(tmp_path)
    bt.pause(p, reply_to="1")                       # mid-work reply: the task keeps its ACK
    assert bt._loop_state(p, os.getpid())[0] == "wait"
    _age(p, paused_at=time.time() - bt.PAUSE_HOLD - 1)
    assert bt._loop_state(p, os.getpid())[0] == "send"
    bt.end_task(p, ack_id="2", spawn=fake_spawn)    # ACK deleted: off for good
    assert bt._loop_state(p, os.getpid())[0] is None


def test_write_retries_while_the_lease_is_open_elsewhere(tmp_path):
    import threading
    p = _one_task(tmp_path)
    f = open(p.lease)                               # the loop reading the lease (Windows denies os.replace)
    threading.Timer(0.1, f.close).start()
    bt.end_task(p, ack_id="2", spawn=fake_spawn)
    assert _lease(p)["tasks"] == {}


def test_loop_retries_a_failed_send_and_survives_one_bad_read(tmp_path, monkeypatch):
    p = _one_task(tmp_path)
    _age(p, pid=os.getpid(), deadline=time.time() - 1)
    calls = []

    def sender(pp, c, t):
        calls.append(1)
        return len(calls) != 1                      # the first send fails
    real = bt._read
    reads = {"n": 0}

    def flaky(pp):
        reads["n"] += 1
        return None if reads["n"] == 3 else real(pp)
    monkeypatch.setattr(bt, "_read", flaky)
    n = {"s": 0}

    def sleeper(_):
        n["s"] += 1
        if n["s"] == 6:
            bt.end_task(p, ack_id="2", spawn=fake_spawn)
    bt.loop(p, sender=sender, sleep=sleeper)
    assert len(calls) >= 3                          # fail + immediate retry + the next cycle (bad read ignored)
