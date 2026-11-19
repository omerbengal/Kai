"""Omer moves to main Kai a request a domain bot already took (19/11/2026, General msgs 5198 / 5201 / 5215).

Main side: auto_ack.py remembers bot-owned General messages and flags a later main tag that replies to one of
them with REDIRECT_FROM_BOT; general_handoff.py stop tags the bot with `🛑 STOP msg=<id>`.
Bot side: bot_tg.auto_ack turns that tag into reason=main_stop (no ACK, no 👀), so the bot aborts its worker."""
import sys

sys.path.insert(0, "D:/Kai/bots")
sys.path.insert(0, "D:/Kai/scripts")
import auto_ack  # noqa: E402
import bot_tg  # noqa: E402
import general_handoff as gh  # noqa: E402

GEN = auto_ack.GENERAL_CHAT_ID
MAIN = auto_ack.MAIN_USERNAME


def ch(content, **attrs):
    base = {"source": "plugin:telegram:telegram", "chat_id": GEN, "message_id": "100", "user": "omer",
            "user_id": "${KAI_TELEGRAM_CHAT_ID}", "ts": "2026-11-19T18:00:00.000Z"}
    base.update(attrs)
    a = " ".join(f'{k}="{v}"' for k, v in base.items())
    return f"<channel {a}>\n{content}\n</channel>"


def _env(monkeypatch, tmp_path, owner=None):
    monkeypatch.setattr(auto_ack, "REGISTRY_DIR", str(tmp_path / "reg"))
    monkeypatch.setattr(auto_ack, "BOT_OWNED_LOG", str(tmp_path / "owned.json"))
    monkeypatch.setattr(auto_ack, "open_bot_questions", lambda: [])
    monkeypatch.setattr(auto_ack, "bot_sender", lambda attrs: None)
    monkeypatch.setattr(auto_ack, "bot_reply_owner", lambda attrs: owner if attrs.get("reply_to_message_id") == "5197" else None)
    sent = []
    monkeypatch.setattr(auto_ack, "send_ack", lambda text, reply_to, chat_id=None: sent.append(reply_to) or 5202)
    # the defaults were bound at def time: point them at the temp log
    monkeypatch.setattr(auto_ack, "remember_bot_owned",
                        lambda mid, bot, now=None, f=auto_ack.remember_bot_owned: f(mid, bot, now, str(tmp_path / "owned.json")))
    monkeypatch.setattr(auto_ack, "redirect_owner",
                        lambda a, c, f=auto_ack.redirect_owner: f(a, c, str(tmp_path / "owned.json")))
    return sent


def test_redirect_after_reply_to_bot_is_flagged(tmp_path, monkeypatch):
    sent = _env(monkeypatch, tmp_path, owner="whatsapp")
    out = auto_ack.handle(ch("[voice]", message_id="5198", reply_to_message_id="5197", attachment_kind="voice"))
    assert sent == [] and "reason=reply_to_bot BOT=whatsapp" in out[0]
    out = auto_ack.handle(ch(f"התכוונתי אליך @{MAIN}", message_id="5201", reply_to_message_id="5198"))
    assert sent == ["5201"]
    assert "AUTO_ACK_MSG_ID=5202" in out[0]
    assert "REDIRECT_FROM_BOT=whatsapp REDIRECTED_MSG=5198" in out[0]


def test_redirect_after_direct_bot_tag_is_flagged(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    auto_ack.handle(ch("@kai__whatsapp_bot תשלח ל<contact>", message_id="600"))
    out = auto_ack.handle(ch(f"@{MAIN} זה בשבילך", message_id="601", reply_to_message_id="600"))
    assert "REDIRECT_FROM_BOT=whatsapp REDIRECTED_MSG=600" in out[0]


def test_main_tag_replying_to_unrelated_msg_is_not_flagged(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    out = auto_ack.handle(ch(f"@{MAIN} תבדוק", message_id="700", reply_to_message_id="699"))
    assert "REDIRECT_FROM_BOT" not in out[0]


def test_reply_without_main_tag_is_not_a_redirect(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path, owner="whatsapp")
    auto_ack.handle(ch("x", message_id="5198", reply_to_message_id="5197"))
    a, c = auto_ack.parse_channels(ch("עוד משהו", message_id="5203", reply_to_message_id="5198"))[0]
    assert auto_ack.redirect_owner(a, c) is None


def test_old_bot_owned_entry_expires(tmp_path):
    p = str(tmp_path / "o.json")
    auto_ack.remember_bot_owned("10", "trading", now=1000, path=p)
    a, c = auto_ack.parse_channels(ch(f"@{MAIN} שלך", message_id="11", reply_to_message_id="10"))[0]
    assert auto_ack.redirect_owner(a, c, path=p, now=1000 + 60) == "trading"
    assert auto_ack.redirect_owner(a, c, path=p, now=1000 + auto_ack.BOT_OWNED_MAX_AGE_S + 1) is None


# --- general_handoff.py stop ----------------------------------------------------------------------------------
BOTS = {"whatsapp": {"username": "kai__whatsapp_bot", "group_id": GEN, "title": "קאי וואטסאפ"}}


def test_stop_tags_the_bot_as_reply_to_the_taken_message():
    sent = []
    st, mid = gh.stop("whatsapp", "5198", bots=BOTS, up=lambda c: True,
                      sender=lambda chat, text, rt: sent.append((chat, text, rt)) or 5205)
    assert (st, mid) == ("tagged", 5205)
    chat, text, rt = sent[0]
    assert chat == GEN and rt == "5198"
    assert text.startswith("@kai__whatsapp_bot 🛑 STOP msg=5198")


def test_stop_on_a_down_bot_posts_nothing():
    sent = []
    assert gh.stop("whatsapp", "5198", bots=BOTS, up=lambda c: False,
                   sender=lambda *a: sent.append(a)) == ("down", None)
    assert sent == []


# --- bot side: the STOP tag becomes reason=main_stop -------------------------------------------------------------
def test_bot_hook_turns_main_stop_into_main_stop_line(tmp_path, monkeypatch):
    c = {"title": "קאי וואטסאפ", "home": str(tmp_path), "state_dir": str(tmp_path / "st"),
         "username": "kai__whatsapp_bot", "group_id": GEN, "topics": ["5"], "topic_name": "💬"}
    monkeypatch.setattr(bot_tg.kai_bots, "bot", lambda name, *a, **k: c)
    sends, reacts = [], []
    text = gh.stop_text("5198")
    prompt = ch(f"@kai__whatsapp_bot {text}", message_id="5205", user_id=bot_tg.MAIN_BOT_ID,
                user="Nova_PersonalAssistant_Bengi_bot", reply_to_message_id="5198")
    out = bot_tg.auto_ack("whatsapp", prompt, sender=lambda *a, **k: sends.append(a) or 1,
                          reactor=lambda *a, **k: reacts.append(a))
    assert sends == [] and reacts == []
    assert "reason=main_stop STOP_TASK_MSG=5198 STOP_TAG_MSG=5205" in out[0]


def test_bot_ignores_stop_text_from_omer(tmp_path, monkeypatch):
    c = {"home": str(tmp_path), "username": "kai__whatsapp_bot", "group_id": GEN}
    monkeypatch.setattr(bot_tg.kai_bots, "bot", lambda name, *a, **k: c)
    a, content = auto_ack.parse_channels(ch("@kai__whatsapp_bot 🛑 STOP msg=1"))[0]
    assert bot_tg.main_stop_target("whatsapp", a, content) is None
