"""Questions and problems go to General with Omer tagged (07/11/2026, Omer msgs 781 / 791).

- general-ask: ONE short message in General that opens with a text_mention of Omer; the tag waits (🤔).
- His answer in General (a reply to the question / done line / nudge, a button on it, or main Kai forwarding a
  plain answer as a reply to it) reopens the ORIGINAL task in the bot (GENERAL_PENDING_TAG), never a new task.
- A question with buttons that stays in the bot's topic gets a "waiting for you" line in General after 2 min,
  with a link to it, unless Omer answered first.
- Main Kai: a reply in General to a domain bot's message is that bot's (no ACK, nothing to do); open
  questions are listed so a plain answer can be routed (general_handoff.py answer).
- The requesting bot never takes the other bot's question to Omer as its answer (BOT_ASKS_OMER).
"""
import json
import os
import sqlite3
import sys

import time

import pytest

sys.path.insert(0, "D:/Kai/bots")
sys.path.insert(0, "D:/Kai/scripts")
import bot_tg  # noqa: E402
import auto_ack as main_ack  # noqa: E402

GROUP = "-1004297864522"
MAIN_ID = "<REDACTED_SECRET>"
OMER = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
WA_ID, TR_ID = "9010629958", "9259854887"
T0 = time.time() - 600


@pytest.fixture
def bots(tmp_path, monkeypatch):
    allb = {
        "whatsapp": {"title": "קאי וואטסאפ", "home": str(tmp_path / "whatsapp"),
                     "username": "kai__whatsapp_bot", "bot_id": WA_ID, "group_id": GROUP,
                     "topics": ["121"], "caps": ["whatsapp"]},
        "trading": {"title": "קאי השקעות", "home": str(tmp_path / "trading"), "username": "kai__invest_bot",
                    "bot_id": TR_ID, "group_id": GROUP, "topics": ["117"], "caps": []},
    }
    for c in allb.values():
        os.makedirs(c["home"], exist_ok=True)
    monkeypatch.setattr(bot_tg.kai_bots, "bot", lambda name, *a, **k: allb[name])
    monkeypatch.setattr(bot_tg.kai_bots, "load_config", lambda *a, **k: allb)
    monkeypatch.setattr(bot_tg, "drop_ack", lambda *a: None)
    monkeypatch.setattr(bot_tg, "_typing", lambda *a, **k: None)
    monkeypatch.setattr(bot_tg, "_spawn_nudge_timer", lambda *a, **k: None)
    return allb


class Rec:
    def __init__(self):
        self.sends, self.reacts, self.regs = [], [], []

    def send(self, bot, chat, text, thread=None, reply_to=None, silent=False, timeout=15, **kw):
        self.sends.append({"bot": bot, "chat": chat, "text": text, "thread": thread, "reply_to": reply_to, **kw})
        return 900 + len(self.sends)

    def react(self, bot, chat, msg, emoji, *a, **k):
        self.reacts.append((bot, str(msg), emoji))
        return True

    def reg(self, chat, mid, text, kb, bot):
        self.regs.append((chat, mid, bot))


def chan(mid, user_id, text, thread=None, reply_to=None, callback=None):
    extra = f' message_thread_id="{thread}"' if thread else ""
    if reply_to:
        extra += f' reply_to_message_id="{reply_to}"'
    if callback:
        extra += f' callback_data="{callback}"'
    return (f'<channel source="plugin:telegram:telegram" chat_id="{GROUP}" message_id="{mid}" user="x"'
            f' user_id="{user_id}"{extra} ts="2026-11-07T19:30:00Z">{text}</channel>')


def mention_of(s):
    ents = s.get("entities") or []
    assert len(ents) == 1 and ents[0]["type"] == "text_mention" and ents[0]["user"]["id"] == int(OMER)
    text16 = s["text"].encode("utf-16-le")
    e = ents[0]
    return text16[e["offset"] * 2:(e["offset"] + e["length"]) * 2].decode("utf-16-le")


# ---- general-ask ----------------------------------------------------------------------------------

def test_general_ask_tags_omer_in_general_and_waits(bots):
    r = Rec()
    bot_tg.auto_ack("whatsapp", chan("751", TR_ID, "@kai__whatsapp_bot תסכם את הדיון"),
                    sender=r.send, reactor=r.react)
    r = Rec()
    q = bot_tg.general_ask("whatsapp", GROUP, "הקבוצה 'דיונים - כללי' לא ב-wa_policy של קאי השקעות. לסכם בכל זאת?",
                           tag_msg="751", keyboard=[[{"text": "כן", "callback_data": "y"}]],
                           sender=r.send, reactor=r.react, registrar=r.reg)
    s = r.sends[0]
    assert s["thread"] is None and s["reply_to"] == "751" and s["chat"] == GROUP
    assert s["text"].startswith("\u200fעומר, ") and mention_of(s) == "עומר"
    assert s["keyboard"] == [[{"text": "כן", "callback_data": "y"}]] and r.regs == [(GROUP, q, "whatsapp")]
    assert ("whatsapp", "751", "🤔") in r.reacts
    t = bot_tg.load_tags("whatsapp")["751"]
    assert t["state"] == "waiting" and t["question"] == str(q) and t["where"] == "general"
    assert bot_tg.asks_omer(s["text"])


def test_general_ask_refuses_a_long_message(bots):
    with pytest.raises(ValueError):
        bot_tg.general_ask("whatsapp", GROUP, "שורה\nשורה\nשורה\nשורה", sender=Rec().send, reactor=Rec().react)
    with pytest.raises(ValueError):
        bot_tg.general_ask("whatsapp", GROUP, "א" * 400, sender=Rec().send, reactor=Rec().react)


def test_general_ask_does_not_double_omers_name(bots):
    r = Rec()
    bot_tg.general_ask("whatsapp", GROUP, "עומר, נעצרתי: אין גישה", sender=r.send, reactor=r.react,
                       registrar=r.reg)
    assert r.sends[0]["text"] == "\u200fעומר, נעצרתי: אין גישה"


def test_failed_done_line_tags_omer_at_the_end(bots):
    r = Rec()
    bot_tg.general_done("trading", GROUP, "500", "לא הצלחתי למשוך נתונים", ok=False, sender=r.send,
                        reactor=r.react)
    s = r.sends[0]
    assert mention_of(s) == "עומר" and s["text"].startswith("\u200fלא הצלחתי")
    assert not bot_tg.asks_omer(s["text"])   # a bot waiting on this answer takes it as the answer
    r = Rec()
    bot_tg.general_done("trading", GROUP, "501", "סיימתי", ok=True, sender=r.send, reactor=r.react)
    assert "entities" not in r.sends[0]


# ---- Omer answers in General -------------------------------------------------------------------------

def _asked(bot="whatsapp", tag="751"):
    bot_tg.auto_ack(bot, chan(tag, TR_ID, "@kai__whatsapp_bot תסכם"), sender=Rec().send, reactor=Rec().react)
    return bot_tg.general_ask(bot, GROUP, "לסכם בכל זאת?", tag_msg=tag, sender=Rec().send, reactor=Rec().react,
                              registrar=Rec().reg)


def test_reply_in_general_reopens_the_original_task(bots):
    q = _asked()
    r = Rec()
    lines = bot_tg.auto_ack("whatsapp", chan("800", OMER, "כן, תסכם", reply_to=q), sender=r.send, reactor=r.react)
    assert "GENERAL_PENDING_TAG=751" in lines[0] and "GENERAL_ANSWER_MSG_ID=800" in lines[0]
    assert "GENERAL_TAG=1" not in lines[0]
    assert r.sends[0]["thread"] == "121" and r.sends[0]["reply_to"] == "800"   # ACK in the topic
    assert ("whatsapp", "800", "👀") in r.reacts and ("whatsapp", "751", "👀") in r.reacts
    t = bot_tg.load_tags("whatsapp")["751"]
    assert t["state"] == "working" and t["ack"] == str(r.sends[0] and 901)
    assert "800" not in bot_tg.load_tags("whatsapp")   # not a new task


def test_short_answer_is_not_trivial_when_it_answers_a_question(bots):
    q = _asked()
    lines = bot_tg.auto_ack("whatsapp", chan("801", OMER, "אוקיי", reply_to=q), sender=Rec().send,
                            reactor=Rec().react)
    assert "AUTO_ACK_MSG_ID=NONE" not in lines[0] and "GENERAL_PENDING_TAG=751" in lines[0]


def test_button_on_general_question_reopens(bots):
    q = _asked()
    lines = bot_tg.auto_ack("whatsapp", chan(str(q), OMER, "[callback: y]", callback="y"), sender=Rec().send,
                            reactor=Rec().react)
    assert "GENERAL_PENDING_TAG=751" in lines[0]


def test_main_forwarded_plain_answer_reopens(bots):
    q = _asked()
    lines = bot_tg.auto_ack("whatsapp", chan("803", MAIN_ID, "@kai__whatsapp_bot כן, תסכם", reply_to=q),
                            sender=Rec().send, reactor=Rec().react)
    assert "GENERAL_PENDING_TAG=751" in lines[0]


def test_unrelated_general_tag_is_still_a_new_task(bots):
    _asked()
    lines = bot_tg.auto_ack("whatsapp", chan("804", MAIN_ID, "@kai__whatsapp_bot משהו אחר"),
                            sender=Rec().send, reactor=Rec().react)
    assert "GENERAL_TAG=1 TAG_MSG_ID=804" in lines[0]


def test_requesting_bot_ignores_the_question_to_omer(bots):
    r = Rec()
    out = bot_tg.ask("trading", "whatsapp", "תסכם את הדיון", sender=r.send, reactor=r.react, wait=0)
    rid = out["request_msg"]
    line = bot_tg.bot_inbound("trading", "whatsapp",
                              {"message_id": "760", "chat_id": GROUP, "reply_to_message_id": str(rid)},
                              "\u200fעומר, לסכם בכל זאת?", reactor=r.react)
    assert "BOT_ASKS_OMER" in line
    assert bot_tg.load_requests("trading")[str(rid)]["state"] == "waiting"
    line = bot_tg.bot_inbound("trading", "whatsapp",
                              {"message_id": "770", "chat_id": GROUP, "reply_to_message_id": str(rid)},
                              "\u200f6 הודעות, רק דאגה", reactor=r.react)
    assert "BOT_ANSWER" in line and bot_tg.load_requests("trading")[str(rid)]["state"] == "answered"


# ---- 2-minute nudge for topic questions ------------------------------------------------------------------

def _db(bot, bots_cfg):
    return os.path.join(bots_cfg[bot]["home"], "bot.db")


def test_topic_question_nudges_after_two_minutes(bots):
    assert bot_tg.schedule_nudge("trading", GROUP, "117", 650, "\u200fלאשר את העסקה?\n\u200fפרטים", now=T0)
    r = Rec()
    no = lambda *a: False  # noqa: E731
    assert bot_tg.nudge_due("trading", now=T0 + 100, sender=r.send, answered=no, claim=lambda *a: True) == []
    sent = bot_tg.nudge_due("trading", now=T0 + 121, sender=r.send, answered=no, claim=lambda *a: True)
    assert sent == [("650", 901)]
    s = r.sends[0]
    assert s["thread"] is None and mention_of(s) == "עומר" and "https://t.me/c/4297864522/117/650" in s["text"]
    assert "לאשר את העסקה?" in s["text"] and "פרטים" not in s["text"]
    assert bot_tg.load_nudges("trading")["650"]["state"] == "sent"
    assert bot_tg.nudge_due("trading", now=T0 + 500, sender=r.send, answered=no, claim=lambda *a: True) == []


def test_nudge_cancelled_when_omer_answered_first(bots):
    bot_tg.schedule_nudge("trading", GROUP, "117", 650, "לאשר?", now=T0)
    bot_tg.save("trading", "incoming", GROUP, "117", 655, "כן")
    r = Rec()
    assert bot_tg.nudge_due("trading", now=T0 + 200, sender=r.send, claim=lambda *a: True) == []
    assert r.sends == [] and bot_tg.load_nudges("trading")["650"]["state"] == "cancelled"


def test_nudge_only_for_the_bots_group_topic(bots):
    assert not bot_tg.schedule_nudge("trading", GROUP, None, 650, "x")
    assert not bot_tg.schedule_nudge("trading", "${KAI_TELEGRAM_CHAT_ID}", "117", 650, "x")


def test_nudge_claim_prevents_a_double_post(bots):
    bot_tg.schedule_nudge("trading", GROUP, "117", 650, "לאשר?", now=T0)
    r = Rec()
    no = lambda *a: False  # noqa: E731
    bot_tg.nudge_due("trading", now=T0 + 200, sender=r.send, answered=no)
    d = bot_tg.load_nudges("trading")
    d["650"]["state"] = "pending"
    bot_tg._save_nudges("trading", d)
    bot_tg.nudge_due("trading", now=T0 + 200, sender=r.send, answered=no)
    assert len(r.sends) == 1


def test_nudge_replies_to_the_waiting_general_tag(bots):
    bot_tg.auto_ack("trading", chan("600", MAIN_ID, "@kai__invest_bot תבדוק"), sender=Rec().send,
                    reactor=Rec().react)
    bot_tg.general_wait("trading", GROUP, "600", question_msg="650", reactor=Rec().react, schedule=False)
    bot_tg.schedule_nudge("trading", GROUP, "117", 650, "לאשר?", now=T0)
    r = Rec()
    bot_tg.nudge_due("trading", now=T0 + 200, sender=r.send, answered=lambda *a: False, claim=lambda *a: True)
    assert r.sends[0]["reply_to"] == "600"
    assert bot_tg.load_tags("trading")["600"]["nudge"] == "901"
    # Omer replies to the nudge in General: the original task reopens
    lines = bot_tg.auto_ack("trading", chan("905", OMER, "כן", reply_to="901"), sender=Rec().send,
                            reactor=Rec().react)
    assert "GENERAL_PENDING_TAG=600" in lines[0]


def test_outgoing_buttons_in_topic_schedule_a_nudge(bots, monkeypatch):
    monkeypatch.setattr(bot_tg, "_buttons", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(bot_tg, "schedule_nudge", lambda *a, **k: calls.append(a))
    data = {"tool_input": {"chat_id": GROUP, "message_thread_id": "117", "text": "לאשר?",
                           "inline_keyboard": [[{"text": "כן", "callback_data": "y"}]]},
            "tool_response": {"content": [{"type": "text", "text": "sent (id: 650)"}]}}
    bot_tg.hook_save("trading", "outgoing", data)
    assert calls and calls[0][1:4] == (GROUP, "117", 650)


# ---- main Kai side -----------------------------------------------------------------------------------

def test_main_skips_omers_reply_to_a_bot_message(bots):
    bot_tg.save("whatsapp", "outgoing", GROUP, None, 755, "\u200fעומר, לסכם?")
    attrs = {"chat_id": GROUP, "message_id": "790", "reply_to_message_id": "755"}
    assert main_ack.bot_reply_owner(attrs) == "whatsapp"
    lines = main_ack.handle(chan("790", OMER, "כן", reply_to="755"), dry_run=True)
    assert "reason=reply_to_bot BOT=whatsapp" in lines[0] and "AUTO_ACK_MSG_ID=NONE" in lines[0]
    # a reply to a message no bot sent (main's own, or Omer's) stays main's
    assert main_ack.bot_reply_owner({"chat_id": GROUP, "message_id": "791", "reply_to_message_id": "700"}) is None
    # a topic message never reaches main anyway; a private one is never a bot's
    assert main_ack.bot_reply_owner({"chat_id": "${KAI_TELEGRAM_CHAT_ID}", "message_id": "5", "reply_to_message_id": "755"}) is None


def test_open_questions_lists_general_asks_and_nudges(bots):
    q = _asked()
    bot_tg.schedule_nudge("trading", GROUP, "117", 650, "לאשר את העסקה?", now=T0)
    bot_tg.nudge_due("trading", now=T0 + 200, sender=Rec().send, answered=lambda *a: False, claim=lambda *a: True)
    qs = bot_tg.open_questions()
    assert ("whatsapp", str(q), "לסכם בכל זאת?") in qs
    assert any(b == "trading" and m == "901" for b, m, _ in qs)
