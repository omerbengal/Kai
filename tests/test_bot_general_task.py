"""Multi-bot stage 3: main Kai tags a bot in General (06/11/2026, Omer msgs 22581 / 22589,
docs/multi_bot_stage3_main_dispatcher.md s.2).

Bot side: a General message (no thread) is a "General task": 👀 on it at once, the ACK and all work go to
the bot's OWN topic, and at the end 👍 (or 👎) + a very short reply in General.
Main side: scripts/general_handoff.py tags the bot, or says it is down."""
import os
import sys

import pytest

sys.path.insert(0, "D:/Kai/bots")
sys.path.insert(0, "D:/Kai/scripts")
import bot_tg  # noqa: E402
import bot_typing as bt  # noqa: E402
import general_handoff as gh  # noqa: E402

GROUP = "<group-id>"
MAIN_ID = "<REDACTED_SECRET>"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    c = {"title": "קאי השקעות", "home": str(tmp_path), "state_dir": str(tmp_path / "st"),
         "username": "kai__invest_bot", "group_id": GROUP, "topics": ["117"], "topic_name": "📈"}
    monkeypatch.setattr(bot_tg.kai_bots, "bot", lambda name, *a, **k: c)
    monkeypatch.setattr(bot_tg, "drop_ack", lambda *a: None)  # never a real deleteMessage from a test
    return c


def general(mid="500", user_id=MAIN_ID, text="@<bot-username> תבדוק את NVDA"):
    return (f'<channel source="plugin:telegram:telegram" chat_id="{GROUP}" message_id="{mid}" '
            f'user="Nova_PersonalAssistant_Bengi_bot" user_id="{user_id}" ts="2026-11-06T07:00:00Z">'
            f'{text}</channel>')


def topic(mid="501"):
    return (f'<channel source="plugin:telegram:telegram" chat_id="{GROUP}" message_id="{mid}" '
            f'user="omer" user_id="${KAI_TELEGRAM_CHAT_ID}" message_thread_id="117" ts="2026-11-06T07:00:00Z">'
            f'תבדוק את NVDA</channel>')


class Rec:
    def __init__(self):
        self.sends, self.reacts = [], []

    def send(self, bot, chat, text, thread=None, reply_to=None, silent=False, timeout=15):
        self.sends.append({"chat": chat, "text": text, "thread": thread, "reply_to": reply_to})
        return 900 + len(self.sends)

    def react(self, bot, chat, msg, emoji):
        self.reacts.append((chat, str(msg), emoji))
        return True


def test_is_general(cfg):
    assert bot_tg.is_general("trading", {"chat_id": GROUP, "message_id": "5"})
    assert not bot_tg.is_general("trading", {"chat_id": GROUP, "message_id": "5", "message_thread_id": "117"})
    assert not bot_tg.is_general("trading", {"chat_id": "${KAI_TELEGRAM_CHAT_ID}", "message_id": "5"})


def test_general_tag_acks_in_own_topic_and_reacts_eyes(cfg):
    r = Rec()
    lines = bot_tg.auto_ack("trading", general(), sender=r.send, reactor=r.react)
    assert r.reacts == [(GROUP, "500", "👀")]
    assert len(r.sends) == 1
    s = r.sends[0]
    # 06/11/2026 (Omer msg 320): the ACK sits in the bot's own topic as a reply quoting the General tag
    assert s["thread"] == "117" and s["reply_to"] == "500" and s["chat"] == GROUP
    assert "AUTO_ACK_MSG_ID=901" in lines[0]
    assert "GENERAL_TAG=1" in lines[0] and "TAG_MSG_ID=500" in lines[0] and "WORK_THREAD_ID=117" in lines[0]


def test_topic_message_unchanged(cfg):
    r = Rec()
    lines = bot_tg.auto_ack("trading", topic(), sender=r.send, reactor=r.react)
    assert r.reacts == []
    assert r.sends[0]["thread"] == "117" and r.sends[0]["reply_to"] == "501"
    assert "GENERAL_TAG" not in lines[0]


def test_trivial_general_tag_still_gets_eyes_but_no_ack(cfg):
    r = Rec()
    lines = bot_tg.auto_ack("trading", general(mid="502", text="תודה"), sender=r.send, reactor=r.react)
    assert r.reacts == [(GROUP, "502", "👀")]
    assert r.sends == []
    assert "AUTO_ACK_MSG_ID=NONE" in lines[0] and "GENERAL_TAG=1" in lines[0]


def test_eyes_failure_never_blocks_the_ack(cfg):
    r = Rec()

    def boom(*a, **k):
        raise RuntimeError("no")
    lines = bot_tg.auto_ack("trading", general(mid="503"), sender=r.send, reactor=boom)
    assert "AUTO_ACK_MSG_ID=901" in lines[0]


def test_general_done_ok(cfg):
    r = Rec()
    mid = bot_tg.general_done("trading", GROUP, "500", "NVDA מעל הממוצע, אין כניסה היום", ok=True,
                              detail_msg="777", sender=r.send, reactor=r.react)
    assert r.reacts == [(GROUP, "500", "👍")]
    s = r.sends[0]
    assert s["thread"] is None and s["reply_to"] == "500"
    assert s["text"].startswith("\u200f") and "NVDA מעל הממוצע" in s["text"]
    assert "https://t.me/c/4297864522/117/777" in s["text"]
    assert mid == 901


def test_general_done_fail_uses_thumbs_down(cfg):
    r = Rec()
    bot_tg.general_done("trading", GROUP, "500", "לא הצלחתי למשוך נתונים", ok=False,
                        sender=r.send, reactor=r.react)
    assert r.reacts == [(GROUP, "500", "👎")]


def test_general_done_refuses_long_summary(cfg):
    r = Rec()
    long = " ".join(["מילה"] * 20)
    with pytest.raises(ValueError):
        bot_tg.general_done("trading", GROUP, "500", long, sender=r.send, reactor=r.react)
    assert r.sends == [] and r.reacts == []
    with pytest.raises(ValueError):
        bot_tg.general_done("trading", GROUP, "500", "שורה\nשנייה", sender=r.send, reactor=r.react)


def test_done_emoji_is_an_allowed_bot_reaction():
    # ReactionTypeEmoji is a fixed list; ✅ is NOT on it (checked 06/11/2026), skin tones are not either
    assert bot_tg.DONE_OK == "👍" and bot_tg.DONE_FAIL == "👎" and bot_tg.SEEN == "👀"


def test_typing_for_general_tag_goes_to_own_topic():
    assert bt.parse_open(general(), GROUP, own_topic="117") == (GROUP, "117", "500")
    assert bt.parse_open(topic(), GROUP, own_topic="117") == (GROUP, "117", "501")


# ---- main side -------------------------------------------------------------

BOTS = {"trading": {"title": "קאי השקעות", "username": "kai__invest_bot", "group_id": GROUP,
                    "topics": ["117"], "topic_name": "📈", "route": "מסחר"},
        "off": {"title": "קאי כבוי", "username": "kai_off_bot", "group_id": GROUP, "topics": ["9"],
                "enabled": False}}


def test_handoff_tags_the_bot_in_general():
    sent = []
    st, mid = gh.handoff("trading", "תבדוק את NVDA", "400", bots=BOTS, up=lambda c: True,
                         sender=lambda chat, text, reply_to: sent.append((chat, text, reply_to)) or 55)
    assert st == "tagged" and mid == 55
    chat, text, reply_to = sent[0]
    assert chat == GROUP and reply_to == "400"
    assert text.startswith("@<bot-username> ") and "תבדוק את NVDA" in text
    assert "העברתי" not in text


def test_handoff_bot_down_says_so_and_does_not_tag():
    sent = []
    st, _ = gh.handoff("trading", "x", "400", bots=BOTS, up=lambda c: False,
                       sender=lambda chat, text, reply_to: sent.append(text) or 56)
    assert st == "down"
    assert "@<bot-username>" not in sent[0] and "לא זמין" in sent[0]


def test_handoff_unknown_bot_raises():
    with pytest.raises(KeyError):
        gh.handoff("nope", "x", "1", bots=BOTS, up=lambda c: True, sender=lambda *a: 1)


def test_route_table_lists_enabled_bots_only():
    t = gh.route_table(BOTS)
    assert "trading" in t and "kai__invest_bot" in t and "מסחר" in t
    assert "kai_off_bot" not in t


def test_real_config_has_route_for_every_bot():
    import kai_bots
    for name, c in kai_bots.load_config().items():
        assert c.get("route"), name
        assert c.get("username") and c.get("topics"), name


def test_bots_accept_main_kai_in_group():
    import json
    import kai_bots
    for name, c in kai_bots.load_config().items():
        acc = json.load(open(os.path.join(c["state_dir"], "access.json"), encoding="utf-8"))
        g = acc["groups"][str(c["group_id"])]
        assert MAIN_ID in g["allowFrom"] and "${KAI_TELEGRAM_CHAT_ID}" in g["allowFrom"], name
        assert g["topics"] == c["topics"], name  # never 'general': only a tag reaches them there


def test_history_reads_the_bots_own_db(tmp_path):
    # main Kai sees what a bot did from the bot's own bot.db (Omer msg 22595), no Telegram delivery
    db = str(tmp_path / "bot.db")
    bot_tg.save("trading", "outgoing", GROUP, "117", 155, "NVDA מעל הממוצע", db_path=db)
    out = gh.history("trading", n=5, db_path=db)
    assert "NVDA מעל הממוצע" in out and "id=155" in out


# --- 06/11/2026 fix (Omer msg 22669): 👍 only when the tagged task is truly finished ---------------------
# The miluim bot answered a General tag with a proposal + button and closed it with 👍; Omer pressed the
# button and the real work only started then. Now: a question = general-wait (🤔, no General line); Omer's
# answer in the topic (callback on / reply to the question or the detail message) reopens the tag (👀) and
# tells the session GENERAL_PENDING_TAG so its finish closes the original tag.

def cb(mid, data="go"):
    return (f'<channel source="plugin:telegram:telegram" chat_id="{GROUP}" message_id="{mid}" '
            f'user="omer" user_id="${KAI_TELEGRAM_CHAT_ID}" message_thread_id="117" callback_data="{data}" '
            f'ts="2026-11-06T07:05:00Z">[callback: {data}]</channel>')


def reply_in_topic(mid, to, text="כן, תתקן"):
    return (f'<channel source="plugin:telegram:telegram" chat_id="{GROUP}" message_id="{mid}" '
            f'user="omer" user_id="${KAI_TELEGRAM_CHAT_ID}" message_thread_id="117" reply_to_message_id="{to}" '
            f'ts="2026-11-06T07:05:00Z">{text}</channel>')


def test_wait_emoji_is_an_allowed_bot_reaction():
    assert bot_tg.WAITING == "🤔"


def test_general_tag_is_registered_as_working(cfg):
    bot_tg.auto_ack("trading", general(mid="600"), sender=Rec().send, reactor=Rec().react)
    assert bot_tg.load_tags("trading")["600"]["state"] == "working"


def test_general_wait_sets_thinking_and_posts_nothing_in_general(cfg):
    r = Rec()
    bot_tg.auto_ack("trading", general(mid="600"), sender=r.send, reactor=r.react)
    r = Rec()
    bot_tg.general_wait("trading", GROUP, "600", question_msg="650", reactor=r.react)
    assert r.reacts == [(GROUP, "600", "🤔")] and r.sends == []
    t = bot_tg.load_tags("trading")["600"]
    assert t["state"] == "waiting" and t["question"] == "650"


def test_general_done_refuses_while_waiting(cfg):
    bot_tg.auto_ack("trading", general(mid="600"), sender=Rec().send, reactor=Rec().react)
    bot_tg.general_wait("trading", GROUP, "600", question_msg="650", reactor=Rec().react)
    r = Rec()
    with pytest.raises(ValueError):
        bot_tg.general_done("trading", GROUP, "600", "סיימתי", sender=r.send, reactor=r.react)
    assert r.sends == [] and r.reacts == []


def test_answer_by_button_reopens_the_waiting_tag(cfg):
    bot_tg.auto_ack("trading", general(mid="600"), sender=Rec().send, reactor=Rec().react)
    bot_tg.general_wait("trading", GROUP, "600", question_msg="650", reactor=Rec().react)
    r = Rec()
    lines = bot_tg.auto_ack("trading", cb("650"), sender=r.send, reactor=r.react)
    assert (GROUP, "600", "👀") in r.reacts
    assert "GENERAL_PENDING_TAG=600" in lines[0]
    assert bot_tg.load_tags("trading")["600"]["state"] == "working"
    # and now the finish closes it normally
    r2 = Rec()
    bot_tg.general_done("trading", GROUP, "600", "תוקן, הכניסה מהירה פי 4", detail_msg="700",
                        sender=r2.send, reactor=r2.react)
    assert r2.reacts == [(GROUP, "600", "👍")]
    assert bot_tg.load_tags("trading")["600"]["state"] == "done"


def test_answer_by_text_reply_reopens_the_waiting_tag(cfg):
    bot_tg.auto_ack("trading", general(mid="600"), sender=Rec().send, reactor=Rec().react)
    bot_tg.general_wait("trading", GROUP, "600", question_msg="650", reactor=Rec().react)
    r = Rec()
    lines = bot_tg.auto_ack("trading", reply_in_topic("660", "650"), sender=r.send, reactor=r.react)
    assert (GROUP, "600", "👀") in r.reacts and "GENERAL_PENDING_TAG=600" in lines[0]


def test_any_topic_message_reopens_the_only_waiting_tag(cfg):
    # Omer answers without quoting: a waiting tag still gets reopened
    bot_tg.auto_ack("trading", general(mid="600"), sender=Rec().send, reactor=Rec().react)
    bot_tg.general_wait("trading", GROUP, "600", question_msg="650", reactor=Rec().react)
    lines = bot_tg.auto_ack("trading", topic(mid="661"), sender=Rec().send, reactor=Rec().react)
    assert "GENERAL_PENDING_TAG=600" in lines[0]


def test_button_on_a_done_detail_message_reopens_it(cfg):
    # the 06/11 case: closed with 👍 although the detail message carried a "go" button
    bot_tg.auto_ack("trading", general(mid="600"), sender=Rec().send, reactor=Rec().react)
    bot_tg.general_done("trading", GROUP, "600", "הצעה בנושא", detail_msg="650",
                        sender=Rec().send, reactor=Rec().react)
    r = Rec()
    lines = bot_tg.auto_ack("trading", cb("650"), sender=r.send, reactor=r.react)
    assert (GROUP, "600", "👀") in r.reacts and "GENERAL_PENDING_TAG=600" in lines[0]


def test_plain_topic_message_does_not_reopen_a_done_tag(cfg):
    bot_tg.auto_ack("trading", general(mid="600"), sender=Rec().send, reactor=Rec().react)
    bot_tg.general_done("trading", GROUP, "600", "סיימתי", detail_msg="650",
                        sender=Rec().send, reactor=Rec().react)
    r = Rec()
    lines = bot_tg.auto_ack("trading", topic(mid="662"), sender=r.send, reactor=r.react)
    assert r.reacts == [] and "GENERAL_PENDING_TAG" not in lines[0]


def test_close_on_ack_delete(cfg):
    # one-off for a session that runs the old instructions: deleting that ACK sets 👍 on the tag
    bot_tg.save_tags("trading", {"600": {"chat": GROUP, "state": "working", "close_on_ack": "901"}})
    r = Rec()
    bot_tg.close_on_ack_delete("trading", "901", reactor=r.react)
    assert r.reacts == [(GROUP, "600", "👍")]
    assert bot_tg.load_tags("trading")["600"]["state"] == "done"
    r = Rec()
    bot_tg.close_on_ack_delete("trading", "999", reactor=r.react)
    assert r.reacts == []


# 06/11/2026 (Omer msg 242): the whatsapp bot's 👀 on tag 236 never showed, and the failure left no trace.
def test_eyes_failure_is_retried_once(cfg):
    r = Rec()
    calls = []

    def flaky(bot, chat, msg, emoji):
        calls.append(emoji)
        if len(calls) == 1:
            raise RuntimeError("timeout")
        return r.react(bot, chat, msg, emoji)
    bot_tg.auto_ack("trading", general(mid="700"), sender=r.send, reactor=flaky)
    assert calls == ["👀", "👀"] and r.reacts == [(GROUP, "700", "👀")]


def test_eyes_failure_is_logged(cfg, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("HTTP 429")
    bot_tg.auto_ack("trading", general(mid="701"), sender=Rec().send, reactor=boom)
    log = open(os.path.join(str(tmp_path), "tmp", "react_failures.log"), encoding="utf-8").read()
    assert "701" in log and "HTTP 429" in log


# A trivial tag closed with a plain `react 👍` stayed "working" in general_tags.json (06/11/2026).
def test_react_done_on_a_working_tag_marks_it_done(cfg):
    bot_tg.auto_ack("trading", general(mid="702"), sender=Rec().send, reactor=Rec().react)
    r = Rec()
    bot_tg.react_tag("trading", GROUP, "702", "👍", reactor=r.react)
    assert r.reacts == [(GROUP, "702", "👍")]
    assert bot_tg.load_tags("trading")["702"]["state"] == "done"


def test_react_done_refuses_a_waiting_tag(cfg):
    bot_tg.auto_ack("trading", general(mid="703"), sender=Rec().send, reactor=Rec().react)
    bot_tg.general_wait("trading", GROUP, "703", question_msg="750", reactor=Rec().react)
    r = Rec()
    with pytest.raises(ValueError):
        bot_tg.react_tag("trading", GROUP, "703", "👍", reactor=r.react)
    assert r.reacts == [] and bot_tg.load_tags("trading")["703"]["state"] == "waiting"


def test_react_on_an_untracked_message_just_reacts(cfg):
    r = Rec()
    bot_tg.react_tag("trading", GROUP, "999", "👍", reactor=r.react)
    assert r.reacts == [(GROUP, "999", "👍")] and "999" not in bot_tg.load_tags("trading")


# 06/11/2026 (Omer msg 273): a General tag closed with a plain `react 👍` left the bot's ACK in its topic.
# The ACK id now lives on the tag, and closing the tag (react 👍/👎, general-done, general-wait) deletes it.
class Del:
    def __init__(self):
        self.deleted = []

    def __call__(self, bot, chat, msg):
        self.deleted.append((chat, str(msg)))


def test_auto_ack_stores_the_ack_on_the_tag(cfg):
    bot_tg.auto_ack("trading", general(mid="800"), sender=Rec().send, reactor=Rec().react)
    assert bot_tg.load_tags("trading")["800"]["ack"] == "901"


def test_react_close_deletes_the_tracked_ack(cfg):
    bot_tg.auto_ack("trading", general(mid="801"), sender=Rec().send, reactor=Rec().react)
    d = Del()
    bot_tg.react_tag("trading", GROUP, "801", "👍", reactor=Rec().react, deleter=d)
    assert d.deleted == [(GROUP, "901")]
    assert not bot_tg.load_tags("trading")["801"].get("ack")


def test_react_eyes_does_not_delete_the_ack(cfg):
    bot_tg.auto_ack("trading", general(mid="802"), sender=Rec().send, reactor=Rec().react)
    d = Del()
    bot_tg.react_tag("trading", GROUP, "802", "👀", reactor=Rec().react, deleter=d)
    assert d.deleted == [] and bot_tg.load_tags("trading")["802"]["ack"] == "901"


def test_general_done_deletes_the_tracked_ack(cfg):
    bot_tg.auto_ack("trading", general(mid="803"), sender=Rec().send, reactor=Rec().react)
    d = Del()
    bot_tg.general_done("trading", GROUP, "803", "סיימתי", sender=Rec().send, reactor=Rec().react, deleter=d)
    assert d.deleted == [(GROUP, "901")]


def test_general_wait_deletes_the_tracked_ack(cfg):
    bot_tg.auto_ack("trading", general(mid="804"), sender=Rec().send, reactor=Rec().react)
    d = Del()
    bot_tg.general_wait("trading", GROUP, "804", question_msg="850", reactor=Rec().react, deleter=d)
    assert d.deleted == [(GROUP, "901")]


def test_ack_delete_failure_never_blocks_the_close(cfg):
    bot_tg.auto_ack("trading", general(mid="805"), sender=Rec().send, reactor=Rec().react)

    def boom(*a):
        raise RuntimeError("message to delete not found")
    r = Rec()
    bot_tg.react_tag("trading", GROUP, "805", "👍", reactor=r.react, deleter=boom)
    assert r.reacts == [(GROUP, "805", "👍")] and bot_tg.load_tags("trading")["805"]["state"] == "done"


# 06/11/2026 11:00 UTC: the auto-ACK hook (8 s limit) was killed in all three bots after the ACK was sent and
# before AUTO_ACK_MSG_ID reached the bot, so nobody knew the ACK id. Every network/DB step in the hook now has
# a short budget, and the ACK id is on the tag right after the send.
def test_hook_eyes_uses_a_short_network_timeout(cfg, monkeypatch):
    seen = []
    monkeypatch.setattr(bot_tg, "react", lambda b, c, m, e, timeout=10: seen.append(timeout) or True)
    bot_tg.auto_ack("trading", general(mid="900"), sender=Rec().send)
    assert seen and max(seen) <= bot_tg.HOOK_NET_TIMEOUT <= 3


def test_send_saves_with_the_short_db_timeout(cfg, monkeypatch):
    got = {}

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true, "result": {"message_id": 42}}'
    monkeypatch.setattr(bot_tg.urllib.request, "urlopen", lambda req, timeout=None: R())
    monkeypatch.setattr(bot_tg, "token", lambda name: "T")
    monkeypatch.setattr(bot_tg, "save", lambda *a, **k: got.update(k))
    assert bot_tg.send("trading", GROUP, "x", thread="117", timeout=3) == 42
    assert got.get("db_timeout") == 3


def test_hook_logs_its_duration(cfg, tmp_path):
    bot_tg.auto_ack("trading", general(mid="901"), sender=Rec().send, reactor=Rec().react)
    log = open(os.path.join(str(tmp_path), "tmp", "auto_ack_timing.log"), encoding="utf-8").read()
    assert "msg=901" in log and "ack=901" in log


def test_hook_tells_the_bot_to_reply_its_summary_to_the_tag(cfg, monkeypatch, capsys):
    import io
    import json as _j
    monkeypatch.setattr(bot_tg, "send", Rec().send)
    monkeypatch.setattr(bot_tg, "_hook_react", Rec().react)
    monkeypatch.setattr(bot_tg.sys, "stdin", io.TextIOWrapper(io.BytesIO(_j.dumps({"prompt": general(mid="910")}).encode())))
    bot_tg.main(["bot_tg.py", "auto-ack", "--bot", "trading"])
    out = capsys.readouterr().out
    assert "reply_to=TAG_MSG_ID" in out and "message_thread_id=WORK_THREAD_ID" in out
