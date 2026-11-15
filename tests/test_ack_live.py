"""bots/ack_live.py - live ACK for domain-bot workers (15/11/2026)."""
import json
import os
import sys

sys.path.insert(0, "D:/Kai/bots")
sys.path.insert(0, "D:/Kai/scripts")
import ack_live as al  # noqa: E402
import new_bot  # noqa: E402

GROUP = "-1004297864522"
BLOCK = f"CHAT_ID: {GROUP}\nTHREAD_ID: 2876\nACK_MSG_ID: 3515\nORIGINAL_MSG_ID: 3514\nTITLE: מיזוג ל-main\nMISSION: x"
T0 = 1_800_000_000.0


class Editor:
    def __init__(self, res="ok"):
        self.calls, self.res = [], res

    def __call__(self, bot, chat, msg, text):
        self.calls.append((chat, str(msg), text))
        return self.res


def resolver_for(block):
    return lambda path: al.parse_block(block)


def call(tmp, tool, tin, now, agent="a1", ed=None, block=BLOCK):
    data = {"tool_name": tool, "tool_input": tin, "session_id": "s", "transcript_path": "x/s.jsonl"}
    if agent:
        data["agent_id"] = agent
    return al.handle("demo", data, tmp_dir=str(tmp), now=now, editor=ed or Editor(),
                     resolver=resolver_for(block))


def test_parse_block():
    assert al.parse_block(BLOCK) == ("3515", "מיזוג ל-main", GROUP)
    assert al.parse_block("no block here") == (None, None, None)


def test_worker_tool_call_edits_with_description_and_time(tmp_path):
    ed = Editor()
    r = call(tmp_path, "Bash", {"command": "flutter test", "description": "מריץ את הבדיקות"}, T0, ed=ed)
    assert r == "edit:ok"
    chat, msg, text = ed.calls[0]
    assert (chat, msg) == (GROUP, "3515")
    assert text.startswith("🔄 מיזוג ל-main — ⏳ מריץ את הבדיקות · עודכן ")
    assert text[-5:-3].isdigit() and text[-3] == ":"


def test_throttle_one_edit_per_minute(tmp_path):
    ed = Editor()
    call(tmp_path, "Bash", {"description": "a"}, T0, ed=ed)
    assert call(tmp_path, "Bash", {"description": "b"}, T0 + 30, ed=ed) == "skip:throttle"
    assert call(tmp_path, "Read", {"file_path": "D:/x/main.dart"}, T0 + 61, ed=ed) == "edit:ok"
    assert "קורא קובץ main.dart" in ed.calls[-1][2]
    assert len(ed.calls) == 2


def test_dispatcher_calls_never_edit(tmp_path):
    ed = Editor()
    assert call(tmp_path, "Bash", {"description": "x"}, T0, agent=None, ed=ed) == "skip:dispatcher"
    assert ed.calls == []


def test_worker_own_edit_wins_for_a_minute(tmp_path):
    ed = Editor()
    call(tmp_path, "Bash", {"description": "a"}, T0, ed=ed)
    call(tmp_path, "mcp__plugin_telegram_telegram__edit_message",
         {"chat_id": GROUP, "message_id": "3515", "text": "🔄 מיזוג — 🔍 בודק קונפליקטים"}, T0 + 100, ed=ed)
    assert call(tmp_path, "Bash", {"description": "b"}, T0 + 130, ed=ed) == "skip:throttle"
    assert call(tmp_path, "Bash", {"description": "c"}, T0 + 161, ed=ed) == "edit:ok"
    assert len(ed.calls) == 2


def test_finishing_calls_and_done_ack_are_not_edited(tmp_path):
    ed = Editor()
    call(tmp_path, "Bash", {"description": "a"}, T0, ed=ed)
    assert call(tmp_path, "mcp__plugin_telegram_telegram__reply", {"text": "done"}, T0 + 70, ed=ed) == \
        "skip:finishing"
    delete = {"command": f"python D:/Kai/bots/bot_tg.py delete --bot demo --chat {GROUP} --msg 3515",
              "description": "מוחק את ה-ACK"}
    assert call(tmp_path, "Bash", delete, T0 + 71, ed=ed) == "skip:done"
    assert call(tmp_path, "Bash", {"description": "late"}, T0 + 200, ed=ed) == "skip:done"
    assert len(ed.calls) == 1


def test_mark_done_from_bot_tg_delete(tmp_path):
    ed = Editor()
    call(tmp_path, "Bash", {"description": "a"}, T0, ed=ed)
    al.mark_done("demo", "3515", tmp_dir=str(tmp_path), now=T0 + 5)
    assert call(tmp_path, "Bash", {"description": "b"}, T0 + 100, ed=ed) == "skip:done"
    assert al.stall_sweep("demo", tmp_dir=str(tmp_path), now=T0 + 2000, editor=ed) == []


def test_deleted_message_closes_it(tmp_path):
    gone = Editor("gone")
    assert call(tmp_path, "Bash", {"description": "a"}, T0, ed=gone) == "edit:gone"
    st = al.load(str(tmp_path))
    assert st["acks"]["3515"]["done"] is True


def test_follow_up_block_moves_to_new_ack(tmp_path):
    ed = Editor()
    call(tmp_path, "Bash", {"description": "a"}, T0, ed=ed)
    new_block = BLOCK.replace("3515", "3600").replace("מיזוג ל-main", "המשך")
    call(tmp_path, "Bash", {"description": "b"}, T0 + 70, ed=ed, block=new_block)
    assert ed.calls[-1][1] == "3600" and "המשך" in ed.calls[-1][2]


def test_two_workers_each_their_own_ack(tmp_path):
    ed = Editor()
    call(tmp_path, "Bash", {"description": "a"}, T0, agent="a1", ed=ed)
    call(tmp_path, "Bash", {"description": "b"}, T0 + 1, agent="a2", ed=ed,
         block=BLOCK.replace("3515", "3700"))
    assert [c[1] for c in ed.calls] == ["3515", "3700"]


def test_stall_marker_after_ten_minutes_and_cleared_by_next_call(tmp_path):
    ed = Editor()
    call(tmp_path, "Bash", {"description": "flutter build"}, T0, ed=ed)
    assert al.stall_sweep("demo", tmp_dir=str(tmp_path), now=T0 + 540, editor=ed) == []
    out = al.stall_sweep("demo", tmp_dir=str(tmp_path), now=T0 + 660, editor=ed)
    assert out == [("3515", "ok")]
    text = ed.calls[-1][2]
    assert "⏳ flutter build" in text and text.endswith("\n⚠️ אין תזוזה 11 דק'")
    # same minute: no second edit; next minute: updated count, one marker only
    assert al.stall_sweep("demo", tmp_dir=str(tmp_path), now=T0 + 690, editor=ed) == []
    al.stall_sweep("demo", tmp_dir=str(tmp_path), now=T0 + 725, editor=ed)
    assert ed.calls[-1][2].count("אין תזוזה") == 1 and ed.calls[-1][2].endswith("12 דק'")
    # a tool call right after a stall edits at once (no throttle) and drops the marker
    assert call(tmp_path, "Bash", {"description": "back"}, T0 + 730, ed=ed) == "edit:ok"
    assert "אין תזוזה" not in ed.calls[-1][2]


def test_stall_for_a_worker_registered_by_the_dispatcher_only(tmp_path):
    ed = Editor()
    call(tmp_path, "Agent", {"prompt": BLOCK, "subagent_type": "demo-worker"}, T0, agent=None, ed=ed)
    assert ed.calls == []
    al.stall_sweep("demo", tmp_dir=str(tmp_path), now=T0 + 601, editor=ed)
    assert ed.calls[-1][1] == "3515" and ed.calls[-1][2].startswith("🔄 מיזוג ל-main — ⏳")


def test_stall_gives_up_after_three_hours(tmp_path):
    ed = Editor()
    call(tmp_path, "Bash", {"description": "a"}, T0, ed=ed)
    assert al.stall_sweep("demo", tmp_dir=str(tmp_path), now=T0 + 3 * 3600 + 120, editor=ed) == []


def test_transcript_newest_block(tmp_path):
    sess = tmp_path / "s"
    (sess / "subagents").mkdir(parents=True)
    path = sess / "subagents" / "agent-a9.jsonl"
    lines = [{"type": "user", "message": {"role": "user", "content": BLOCK}},
             {"type": "user", "message": {"content": [{"type": "tool_result",
                                                       "content": "ACK_MSG_ID: 1111\nTITLE: noise"}]}},
             {"type": "user", "message": {"content": [{"type": "text",
                                                       "text": BLOCK.replace("3515", "3800")}]}}]
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", encoding="utf-8")
    p = al.agent_transcript(str(tmp_path / "s.jsonl"), "s", "a9")
    assert os.path.normpath(p) == os.path.normpath(str(path))
    assert al.block_from_transcript(p) == ("3800", "מיזוג ל-main", GROUP)
    assert al.block_from_transcript(str(tmp_path / "missing.jsonl")) == (None, None, None)


def test_step_labels():
    assert al.step_label("Grep", {"pattern": "x"}) == "מחפש בקוד"
    assert al.step_label("mcp__playwright__browser_click", {}) == "עובד בדפדפן"
    assert al.step_label("Bash", {"command": "ls"}) == "Bash"
    assert len(al.step_label("Bash", {"description": "x" * 200})) == al.STEP_MAX


def test_hook_never_raises_on_garbage(tmp_path):
    assert call(tmp_path, "Bash", None, T0, ed=Editor()) in ("edit:ok",)
    assert al.handle("demo", {}, tmp_dir=str(tmp_path), now=T0, editor=Editor(),
                     resolver=lambda p: (None, None, None)) == "skip:dispatcher"


def test_every_bot_settings_has_the_async_hook():
    import kai_bots
    for name in kai_bots.load_config():
        s = new_bot.settings(name, [], kai_bots.bot(name))
        hooks = [h for grp in s["hooks"]["PreToolUse"] for h in grp["hooks"]
                 if "ack_live.py hook" in h["command"]]
        assert len(hooks) == 1, name
        assert hooks[0]["async"] is True and f"--bot {name}" in hooks[0]["command"]


FOLLOW_UP = ("The coordinator sent a message while you were working:\nRound 8 from Omer (msg 3549)...\n"
             "ACK for this round: 3550 (I pin it). Edit it at EVERY step. ... delete ACK 3550.")


def test_free_form_follow_up_ack():
    assert al.parse_block(FOLLOW_UP) == ("3550", None, None)
    assert al.parse_block("ORIGINAL_MSG_ID: 3549 only") == (None, None, None)


def test_follow_up_takes_title_from_the_workers_own_edit(tmp_path):
    ed = Editor()
    call(tmp_path, "Bash", {"description": "a"}, T0, ed=ed)
    call(tmp_path, "SendMessage", {"to": "a1", "message": FOLLOW_UP}, T0 + 5, agent=None, ed=ed)
    call(tmp_path, "mcp__plugin_telegram_telegram__edit_message",
         {"chat_id": GROUP, "message_id": "3550", "text": "🔄 סבב 8 — ⚙️ מתחיל"}, T0 + 6, ed=ed,
         block=FOLLOW_UP)   # the transcript's newest task message is the follow-up
    call(tmp_path, "Bash", {"description": "flutter test"}, T0 + 70, ed=ed, block=FOLLOW_UP)
    chat, msg, text = ed.calls[-1]
    assert (chat, msg) == (GROUP, "3550") and text.startswith("🔄 סבב 8 — ⏳ flutter test")
