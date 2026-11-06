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
    assert s["thread"] == "117" and s["reply_to"] is None and s["chat"] == GROUP
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
