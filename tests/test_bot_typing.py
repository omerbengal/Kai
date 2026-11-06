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
    lease["last_activity"] = time.time() - bt.ACTIVITY_WINDOW - 1
    json.dump(lease, open(p.lease, "w"))
    assert bt._lease_alive(p, os.getpid()) is None         # bounded: no tool call for a minute = off


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
