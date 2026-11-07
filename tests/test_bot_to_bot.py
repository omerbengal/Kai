"""Bot-to-bot requests through General (06/11/2026, Omer msgs 358 / 361: "חלוקת תפקידים טהורה").

A bot that needs another bot's domain (WhatsApp first) posts in the group's General topic, tagging the
target bot (`bot_tg.py ask`). The target treats a tag from one of OUR bots like main Kai's tag (👀, works in
its own topic, general-done replies to the tag in General). The requester's auto-ACK hook captures that
reply for the waiting `ask` (no ACK, no new task). Loop guards: never self, never main Kai, depth 1 (no
asking while answering a bot), rate limits both ways, bot chatter that is not a tag / an answer is ignored."""
import json
import os
import sys

import pytest

sys.path.insert(0, "D:/Kai/bots")
sys.path.insert(0, "D:/Kai/scripts")
import bot_tg  # noqa: E402

GROUP = "<group-id>"
OMER = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
MAIN_ID = "<REDACTED_SECRET>"
MIL_ID, WA_ID, TR_ID = "9025640099", "9010629958", "9259854887"


@pytest.fixture
def bots(tmp_path, monkeypatch):
    allb = {
        "miluim": {"title": "קאי מילואים 🪖", "home": str(tmp_path / "miluim"), "username": "<bot-username>",
                   "bot_id": MIL_ID, "group_id": GROUP, "topics": ["2"], "caps": []},
        "whatsapp": {"title": "קאי וואטסאפ", "home": str(tmp_path / "whatsapp"),
                     "username": "<bot-username>", "bot_id": WA_ID, "group_id": GROUP,
                     "topics": ["121"], "caps": ["whatsapp", "contacts_owner"]},
        "trading": {"title": "קאי השקעות", "home": str(tmp_path / "trading"), "username": "<bot-username>",
                    "bot_id": TR_ID, "group_id": GROUP, "topics": ["117"], "caps": []},
    }
    for c in allb.values():
        os.makedirs(c["home"], exist_ok=True)
    monkeypatch.setattr(bot_tg.kai_bots, "bot", lambda name, *a, **k: allb[name])
    monkeypatch.setattr(bot_tg.kai_bots, "load_config", lambda *a, **k: allb)
    monkeypatch.setattr(bot_tg, "drop_ack", lambda *a: None)
    return allb


class Rec:
    def __init__(self):
        self.sends, self.reacts = [], []

    def send(self, bot, chat, text, thread=None, reply_to=None, silent=False, timeout=15, **kw):
        self.sends.append({"bot": bot, "chat": chat, "text": text, "thread": thread, "reply_to": reply_to, **kw})
        return 900 + len(self.sends)

    def react(self, bot, chat, msg, emoji, *a, **k):
        self.reacts.append((bot, str(msg), emoji))
        return True


def chan(mid, user_id, text, user="x", thread=None, reply_to=None):
    extra = f' message_thread_id="{thread}"' if thread else ""
    if reply_to:
        extra += f' reply_to_message_id="{reply_to}"'
    return (f'<channel source="plugin:telegram:telegram" chat_id="{GROUP}" message_id="{mid}" user="{user}"'
            f' user_id="{user_id}"{extra} ts="2026-11-06T13:10:00Z">{text}</channel>')


# ---- ask (requester side) -------------------------------------------------------------------------

def test_ask_posts_a_tag_in_general(bots):
    r = Rec()
    out = bot_tg.ask("miluim", "whatsapp", "כמה הודעות היו היום בקבוצת האירוע?", sender=r.send,
                     reactor=r.react, wait=0)
    s = r.sends[0]
    assert s["bot"] == "miluim" and s["chat"] == GROUP and s["thread"] is None and s["reply_to"] is None
    assert s["text"].startswith("@<bot-username> ") and "קבוצת האירוע" in s["text"]
    assert out["request_msg"] == 901 and out["status"] == "waiting"
    req = bot_tg.load_requests("miluim")["901"]
    assert req["to"] == "whatsapp" and req["state"] == "waiting"


def test_ask_refuses_self_main_and_unknown(bots):
    r = Rec()
    for target in ("miluim", "main", "nobody"):
        with pytest.raises(ValueError):
            bot_tg.ask("miluim", target, "x", sender=r.send, reactor=r.react, wait=0)
    assert r.sends == []


def test_ask_marks_origin_tag_thinking(bots):
    r = Rec()
    bot_tg.ask("miluim", "whatsapp", "x", origin_tag="400", sender=r.send, reactor=r.react, wait=0)
    assert ("miluim", "400", "🤔") in r.reacts


def test_ask_rate_limit(bots):
    r = Rec()
    for i in range(bot_tg.ASK_MAX_PER_WINDOW):
        bot_tg.ask("miluim", "whatsapp", f"q{i}", sender=r.send, reactor=r.react, wait=0)
    with pytest.raises(ValueError):
        bot_tg.ask("miluim", "whatsapp", "one more", sender=r.send, reactor=r.react, wait=0)


def test_depth_one_no_ask_while_answering_a_bot(bots):
    r = Rec()
    # whatsapp got a tag from miluim (a bot) and is working on it
    bot_tg.auto_ack("whatsapp", chan("700", MIL_ID, "@<bot-username> כמה הודעות היום?",
                                     user="kai__miluim_bot"), sender=r.send, reactor=r.react)
    with pytest.raises(ValueError) as e:
        bot_tg.ask("whatsapp", "trading", "x", sender=r.send, reactor=r.react, wait=0)
    assert "depth" in str(e.value)


# ---- target side: a tag from another of our bots ---------------------------------------------------

def test_target_treats_bot_tag_like_main_tag(bots):
    r = Rec()
    lines = bot_tg.auto_ack("whatsapp", chan("700", MIL_ID, "@<bot-username> כמה הודעות היום?",
                                             user="kai__miluim_bot"), sender=r.send, reactor=r.react)
    assert ("whatsapp", "700", "👀") in r.reacts
    assert r.sends[0]["thread"] == "121" and r.sends[0]["reply_to"] == "700"
    assert "GENERAL_TAG=1" in lines[0] and "FROM_BOT=miluim" in lines[0]
    assert bot_tg.load_tags("whatsapp")["700"]["from_bot"] == "miluim"


def test_bot_chatter_is_ignored(bots):
    r = Rec()
    # a General line of another bot that neither tags me nor answers my request
    lines = bot_tg.auto_ack("whatsapp", chan("701", TR_ID, "‏NVDA מעל הממוצע", reply_to="650"),
                            sender=r.send, reactor=r.react)
    assert r.sends == [] and r.reacts == []
    assert "AUTO_ACK_MSG_ID=NONE" in lines[0] and "BOT_CHATTER" in lines[0]
    # a topic message of another bot (it quotes something of mine) is chatter too
    lines = bot_tg.auto_ack("whatsapp", chan("702", TR_ID, "‏פירוט", thread="117", reply_to="650"),
                            sender=r.send, reactor=r.react)
    assert r.sends == [] and "BOT_CHATTER" in lines[0]


def test_stranger_in_group_gets_nothing(bots):
    r = Rec()
    lines = bot_tg.auto_ack("whatsapp", chan("703", "12345", "@<bot-username> תשלח"),
                            sender=r.send, reactor=r.react)
    assert r.sends == [] and r.reacts == [] and "AUTO_ACK_MSG_ID=NONE" in lines[0]


def test_inbound_bot_tag_rate_limit(bots):
    r = Rec()
    for i in range(bot_tg.BOT_TAGS_MAX_PER_WINDOW):
        bot_tg.auto_ack("whatsapp", chan(str(800 + i), MIL_ID, f"@<bot-username> q{i}"),
                        sender=r.send, reactor=r.react)
    n = len(r.sends)
    lines = bot_tg.auto_ack("whatsapp", chan("899", MIL_ID, "@<bot-username> again"),
                            sender=r.send, reactor=r.react)
    assert len(r.sends) == n and "bot_rate_limit" in lines[0]


def test_general_done_for_a_bot_tag_may_carry_the_answer(bots):
    r = Rec()
    bot_tg.auto_ack("whatsapp", chan("700", MIL_ID, "@<bot-username> כמה הודעות היום?"),
                    sender=r.send, reactor=r.react)
    answer = "היום 14 הודעות בקבוצת האירוע.\nאחרונה 15:02 מדנה: מי עוד צריך כרטיס לשבת?"
    bot_tg.general_done("whatsapp", GROUP, "700", answer, sender=r.send, reactor=r.react)
    s = r.sends[-1]
    assert s["thread"] is None and s["reply_to"] == "700" and "14 הודעות" in s["text"]
    # an answer for Omer (main Kai's tag) stays one short line
    bot_tg.auto_ack("whatsapp", chan("710", MAIN_ID, "@<bot-username> משהו"), sender=r.send,
                    reactor=r.react)
    with pytest.raises(ValueError):
        bot_tg.general_done("whatsapp", GROUP, "710", answer, sender=r.send, reactor=r.react)


# ---- requester side: capturing the answer ----------------------------------------------------------

def test_answer_is_captured_for_the_waiting_ask(bots):
    r = Rec()
    out = bot_tg.ask("miluim", "whatsapp", "כמה הודעות?", origin_tag="400", sender=r.send, reactor=r.react,
                     wait=0)
    rid = str(out["request_msg"])
    r.reacts.clear()
    # whatsapp's ACK in its own topic quoting the tag: neither an answer nor a task
    lines = bot_tg.auto_ack("miluim", chan("950", WA_ID, "🔄 ⏳", thread="121", reply_to=rid),
                            sender=r.send, reactor=r.react)
    assert "AUTO_ACK_MSG_ID=NONE" in lines[0] and len(r.sends) == 1
    # the detailed reply in its topic (if Telegram delivers it): kept as a detail
    bot_tg.auto_ack("miluim", chan("951", WA_ID, "‏פירוט: 14 הודעות", thread="121", reply_to=rid),
                    sender=r.send, reactor=r.react)
    assert bot_tg.load_requests("miluim")[rid]["state"] == "waiting"
    # the General reply = the answer
    lines = bot_tg.auto_ack("miluim", chan("952", WA_ID, "‏היום 14 הודעות", reply_to=rid),
                            sender=r.send, reactor=r.react)
    assert len(r.sends) == 1                                  # no ACK for an answer
    assert "BOT_ANSWER" in lines[0] and f"request={rid}" in lines[0] and "AUTO_ACK_MSG_ID=NONE" in lines[0]
    req = bot_tg.load_requests("miluim")[rid]
    assert req["state"] == "answered" and "14 הודעות" in req["answer"]
    assert [d["text"] for d in req["details"]] == ["‏פירוט: 14 הודעות"]
    assert ("miluim", "400", "👀") in r.reacts                # the origin tag is back to working
    res = bot_tg.ask_wait("miluim", rid, timeout=0)
    assert res["status"] == "answered" and "14 הודעות" in res["answer"]


def test_answer_from_the_wrong_bot_is_not_taken(bots):
    r = Rec()
    out = bot_tg.ask("miluim", "whatsapp", "x", sender=r.send, reactor=r.react, wait=0)
    rid = str(out["request_msg"])
    lines = bot_tg.auto_ack("miluim", chan("960", TR_ID, "‏תשובה מזויפת", reply_to=rid),
                            sender=r.send, reactor=r.react)
    assert "BOT_CHATTER" in lines[0]
    assert bot_tg.load_requests("miluim")[rid]["state"] == "waiting"


def test_ask_wait_times_out_with_a_clear_status(bots):
    r = Rec()
    out = bot_tg.ask("miluim", "whatsapp", "x", sender=r.send, reactor=r.react, wait=0)
    res = bot_tg.ask_wait("miluim", str(out["request_msg"]), timeout=0)
    assert res["status"] == "waiting" and "ask-wait" in res["note"]


def test_omer_messages_unchanged(bots):
    r = Rec()
    lines = bot_tg.auto_ack("miluim", chan("970", OMER, "תבדוק משהו", thread="2"), sender=r.send,
                            reactor=r.react)
    assert "AUTO_ACK_MSG_ID=901" in lines[0] and "BOT_" not in lines[0]


# ---- generated instructions ------------------------------------------------------------------------

def test_generated_instructions_say_whatsapp_is_a_general_tag():
    import new_bot
    for name in ("miluim", "trading"):
        md = new_bot.render(name)["CLAUDE.md"]
        assert "bot_tg.py ask --bot " + name + " --to whatsapp" in md
        assert "bus.py read" not in md and "bus.py draft" not in md
        assert "BOT_ANSWER" in md and "BOT_CHATTER" in md
        worker = new_bot.render(name)[f".claude/agents/{name}-worker.md"]
        assert "bot_tg.py ask" in worker
    wa = new_bot.render("whatsapp")["CLAUDE.md"]
    assert "FROM_BOT" in wa and "never counts as approval" in wa


def test_no_typing_for_another_bots_answer():
    import bot_typing as bt
    ans = chan("952", WA_ID, "‏היום 14 הודעות", reply_to="901")
    assert bt.parse_open(ans, GROUP, own_topic="2") is None
    tag = chan("700", MIL_ID, "@<bot-username> כמה הודעות?")
    assert bt.parse_open(tag, GROUP, own_topic="121") == (GROUP, "121", "700")
