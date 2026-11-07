import json
import os
import subprocess
import sys

sys.path.insert(0, "D:/Kai/scripts")
import auto_ack  # noqa: E402


def ch(content, **attrs):
    base = {"source": "plugin:telegram:telegram", "chat_id": "${KAI_TELEGRAM_CHAT_ID}", "message_id": "100", "user": "omer",
            "ts": "2026-10-22T10:00:00.000Z"}
    base.update(attrs)
    a = " ".join(f'{k}="{v}"' for k, v in base.items())
    return f"<channel {a}>\n{content}\n</channel>"


def cls(prompt):
    (attrs, content), = auto_ack.parse_channels(prompt)
    return auto_ack.classify(attrs, content)


def test_text_gets_text_ack():
    assert cls(ch("תזכיר לי מחר בעשר לקנות חלב")) == ("ack", "🔄 ⏳")


def test_voice_gets_voice_ack():
    p = ch("(voice message)", attachment_kind="voice", attachment_file_id="abc")
    assert cls(p) == ("ack", "🎤 מתמלל... ⏳")


def test_document_gets_text_ack():
    assert cls(ch("קובץ", attachment_kind="document", attachment_file_id="x")) == ("ack", "🔄 ⏳")


def test_reaction_no_ack():
    p = ch("[reaction on message 55] added: 👍", event_type="reaction")
    assert cls(p) == ("none", "reaction")


def test_callback_gets_ack():
    p = ch('[callback: ack_hook_build] על: "שאלה"', callback_data="ack_hook_build")
    assert cls(p) == ("ack", "🔄 ⏳")


def test_trivial_allow_list():
    for t in ["היי", "שלום", "בוקר טוב", "לילה טוב", "Hi", "hello!", "תודה", "תודה!", "אוקיי",
              "בסדר", "OK", "Got it", "אתה פה?", "פה?", "ער?", "קאי?", "שומע?", "You there?", "\u200fתודה"]:
        assert cls(ch(t)) == ("none", "trivial"), t


def test_not_trivial():
    for t in ["תודה, תזכיר לי מחר", "היי קאי מה המצב?", "בסדר?", "שלום ל<contact>", "איפה הודעת ה-ACK?!?",
              "OK send it", "5", "מה השעה?"]:
        assert cls(ch(t))[0] == "ack", t


def test_image_never_trivial():
    assert cls(ch("תודה", image_path="C:/x.jpg")) == ("ack", "🔄 ⏳")


def test_other_chat_no_ack():
    assert cls(ch("משהו", chat_id="-100123")) == ("none", "other_chat")


def test_non_channel_prompt_has_no_output():
    assert auto_ack.handle("<task-notification>agent done</task-notification>", dry_run=True) == []
    assert auto_ack.handle("run the cron job", dry_run=True) == []


def test_burst_two_channels():
    p = ch("תבדוק את המייל", message_id="1") + "\n" + ch("תודה", message_id="2")
    lines = auto_ack.handle(p, dry_run=True)
    assert "message_id=1 AUTO_ACK_MSG_ID=DRYRUN" in lines[0]
    assert "message_id=2 AUTO_ACK_MSG_ID=NONE reason=trivial" in lines[1]


def test_callback_key_distinguishes_presses():
    a = {"message_id": "9", "callback_data": "yes", "ts": "t1"}
    b = {"message_id": "9", "callback_data": "yes", "ts": "t2"}
    assert auto_ack.registry_key(a) != auto_ack.registry_key(b)
    assert auto_ack.registry_key({"message_id": "123"}) == "msg_123"


def test_idempotent_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_ack, "REGISTRY_DIR", str(tmp_path))
    sent = []
    monkeypatch.setattr(auto_ack, "send_ack", lambda text, reply_to, chat_id=None: sent.append(reply_to) or 777)
    p = ch("תבדוק משהו", message_id="42")
    first = auto_ack.handle(p)
    second = auto_ack.handle(p)
    assert sent == ["42"]
    assert "AUTO_ACK_MSG_ID=777" in first[0]
    assert "AUTO_ACK_MSG_ID=777 (already sent)" in second[0]


def test_send_failure_reports_failed_and_allows_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_ack, "REGISTRY_DIR", str(tmp_path))

    def boom(text, reply_to, chat_id=None):
        raise TimeoutError()
    monkeypatch.setattr(auto_ack, "send_ack", boom)
    p = ch("תבדוק משהו", message_id="43")
    assert "AUTO_ACK_MSG_ID=FAILED" in auto_ack.handle(p)[0]
    assert os.listdir(tmp_path) == []  # claim released


def test_cli_dry_run():
    prompt = ch("תבדוק את הלוז שלי", message_id="5000")
    r = subprocess.run([sys.executable, "D:/Kai/scripts/auto_ack.py", "--dry-run"],
                       input=json.dumps({"prompt": prompt}).encode("utf-8"), capture_output=True, timeout=10)
    out = r.stdout.decode("utf-8")
    assert "AUTO_ACK message_id=5000 AUTO_ACK_MSG_ID=DRYRUN" in out


def test_real_plugin_source_and_legacy_source():
    assert cls(ch("תבדוק את המייל")) == ("ack", "🔄 ⏳")
    assert cls(ch("תבדוק את המייל", source="telegram")) == ("ack", "🔄 ⏳")
    assert auto_ack.parse_channels(ch("x", source="plugin:discord:discord")) == []


def test_gt_inside_quoted_attribute():
    p = ch("[callback: yes] על: x", callback_data="yes", callback_original_text="a -> b > c")
    assert cls(p) == ("ack", "🔄 ⏳")


# --- General topic of the bots group (06/11/2026, Omer msg 22689) -----------------------------------
GEN = auto_ack.GENERAL_CHAT_ID


def test_general_text_gets_text_ack():
    assert cls(ch("תבדוק את הלוז", chat_id=GEN)) == ("ack", "🔄 ⏳")


def test_general_voice_gets_voice_ack():
    assert cls(ch("(voice message)", chat_id=GEN, attachment_kind="voice")) == ("ack", "🎤 מתמלל... ⏳")


def test_general_trivial_and_reaction_still_no_ack():
    assert cls(ch("תודה", chat_id=GEN)) == ("none", "trivial")
    assert cls(ch("[reaction on message 5] 👍", chat_id=GEN, event_type="reaction")) == ("none", "reaction")


def test_group_other_topic_no_ack():
    assert cls(ch("משהו", chat_id=GEN, message_thread_id="7")) == ("none", "other_chat")


def test_general_registry_key_never_collides_with_private():
    priv = auto_ack.parse_channels(ch("x", message_id="300"))[0][0]
    gen = auto_ack.parse_channels(ch("x", message_id="300", chat_id=GEN))[0][0]
    assert auto_ack.registry_key(priv) == "msg_300"
    assert auto_ack.registry_key(gen) != auto_ack.registry_key(priv)


def test_general_ack_sent_to_group_without_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_ack, "REGISTRY_DIR", str(tmp_path))
    monkeypatch.setattr(auto_ack, "open_bot_questions", lambda: [])   # live bots' open questions are not this test
    calls = []
    monkeypatch.setattr(auto_ack, "send_ack",
                        lambda text, reply_to, chat_id=None: calls.append((text, reply_to, chat_id)) or 55)
    out = auto_ack.handle(ch("(voice message)", chat_id=GEN, message_id="301", attachment_kind="voice"))
    assert calls == [("🎤 מתמלל... ⏳", "301", GEN)]
    assert "AUTO_ACK_MSG_ID=55" in out[0] and f"chat_id={GEN}" in out[0]


def test_send_ack_general_payload_and_no_db_row(monkeypatch):
    import io
    import urllib.request
    import tg_silent_send
    seen, saved = {}, []

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen.update(json.loads(req.data.decode("utf-8")))
        return R(b'{"ok":true,"result":{"message_id":9}}')
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(tg_silent_send, "save", lambda *a: saved.append(a))
    assert auto_ack.send_ack("🔄 ⏳", "301", chat_id=GEN) == 9
    assert seen["chat_id"] == GEN and "message_thread_id" not in seen
    assert seen["reply_parameters"]["message_id"] == 301 and seen["disable_notification"] is True
    assert saved == []                    # group ids collide with private ones: no messages row
    assert auto_ack.send_ack("🔄 ⏳", "302") == 9
    assert seen["chat_id"] == "${KAI_TELEGRAM_CHAT_ID}" and saved == [(9, "🔄 ⏳")]


# --- bot -> main Kai handoff (07/11/2026, Omer msgs 842 / 847) ----------------------------------------------
WA_BOT = "9010629958"


def _bots(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_ack, "bot_sender",
                        lambda attrs: "whatsapp" if attrs.get("user_id") == WA_BOT and auto_ack.is_general(attrs)
                        else None)
    monkeypatch.setattr(auto_ack, "BOT_HANDOFF_LOG", str(tmp_path / "h.json"))
    monkeypatch.setattr(auto_ack, "bot_handoff_allowed",
                        lambda now=None, path=None, record=True: True)
    monkeypatch.setattr(auto_ack, "open_bot_questions", lambda: [])
    monkeypatch.setattr(auto_ack, "bot_reply_owner", lambda attrs: "whatsapp")   # must be skipped for bots


def test_bot_tag_of_main_is_a_handoff_task(tmp_path, monkeypatch):
    _bots(monkeypatch, tmp_path)
    monkeypatch.setattr(auto_ack, "REGISTRY_DIR", str(tmp_path))
    monkeypatch.setattr(auto_ack, "send_ack", lambda text, reply_to, chat_id=None: 77)
    out = auto_ack.handle(ch(f"@{auto_ack.MAIN_USERNAME} תבנה שליחת תמונה", chat_id=GEN, message_id="840",
                             user_id=WA_BOT))
    assert "AUTO_ACK_MSG_ID=77" in out[0] and "FROM_BOT=whatsapp" in out[0] and "BOT_HANDOFF=1" in out[0]


def test_bot_message_without_main_tag_is_chatter(tmp_path, monkeypatch):
    _bots(monkeypatch, tmp_path)
    out = auto_ack.handle(ch("‏סיכמתי את הדיון", chat_id=GEN, message_id="841", user_id=WA_BOT))
    assert "reason=bot_chatter" in out[0] and "AUTO_ACK_MSG_ID=NONE" in out[0]


def test_bot_handoff_rate_limit(tmp_path):
    p = str(tmp_path / "h.json")
    assert all(auto_ack.bot_handoff_allowed(now=1000 + i, path=p) for i in range(auto_ack.BOT_HANDOFF_MAX_PER_HOUR))
    assert not auto_ack.bot_handoff_allowed(now=1100, path=p)
    assert auto_ack.bot_handoff_allowed(now=1000 + 3601 + auto_ack.BOT_HANDOFF_MAX_PER_HOUR, path=p)


def test_omer_is_never_a_bot_sender():
    attrs = auto_ack.parse_channels(ch("x", chat_id=GEN, user_id="${KAI_TELEGRAM_CHAT_ID}"))[0][0]
    assert auto_ack.bot_sender(attrs) is None


# --- Omer tagging a domain bot directly in General (07/11/2026, Omer msgs 916 / 921 / 925) --------------------
def _no_live(monkeypatch, tmp_path, owner=None):
    monkeypatch.setattr(auto_ack, "REGISTRY_DIR", str(tmp_path))
    monkeypatch.setattr(auto_ack, "open_bot_questions", lambda: [])
    monkeypatch.setattr(auto_ack, "bot_reply_owner", lambda attrs: owner)
    sent = []
    monkeypatch.setattr(auto_ack, "send_ack",
                        lambda text, reply_to, chat_id=None: sent.append((text, reply_to)) or 88)
    return sent


def test_usernames_come_from_bots_config():
    names = auto_ack._bot_usernames()
    assert names.get("kai__miluim_bot") == "miluim"
    assert names.get("kai__whatsapp_bot") == "whatsapp"


def test_direct_bot_tag_sends_no_ack(tmp_path, monkeypatch):
    sent = _no_live(monkeypatch, tmp_path)
    out = auto_ack.handle(ch("@kai__miluim_bot תבדוק את הרישום", chat_id=GEN, message_id="916"))
    assert sent == []
    assert "AUTO_ACK_MSG_ID=NONE reason=direct_bot_tag BOT=miluim" in out[0] and "do NOTHING" in out[0]


def test_direct_bot_tag_mid_text_any_case(tmp_path, monkeypatch):
    sent = _no_live(monkeypatch, tmp_path)
    out = auto_ack.handle(ch("תעשה לי טובה @Kai__WhatsApp_Bot, תשלח ל<contact>", chat_id=GEN, message_id="917"))
    assert sent == [] and "BOT=whatsapp" in out[0]


def test_no_tag_still_gets_ack(tmp_path, monkeypatch):
    sent = _no_live(monkeypatch, tmp_path)
    out = auto_ack.handle(ch("תבדוק את הלוז שלי", chat_id=GEN, message_id="918"))
    assert sent == [("🔄 ⏳", "918")] and "AUTO_ACK_MSG_ID=88" in out[0]


def test_bot_tag_in_private_chat_is_not_bot_owned():
    attrs, content = auto_ack.parse_channels(ch("@kai__miluim_bot משהו"))[0]
    assert auto_ack.direct_bot_tag(attrs, content) is None


def test_partial_username_or_email_is_not_a_tag():
    attrs, _ = auto_ack.parse_channels(ch("x", chat_id=GEN))[0]
    u = {"kai__miluim_bot": "miluim"}
    assert auto_ack.direct_bot_tag(attrs, "@kai__miluim_bot_old", u) is None
    assert auto_ack.direct_bot_tag(attrs, "a@kai__miluim_bot", u) is None


def test_main_tagged_too_keeps_ack(tmp_path, monkeypatch):
    sent = _no_live(monkeypatch, tmp_path)
    auto_ack.handle(ch(f"@{auto_ack.MAIN_USERNAME} @kai__miluim_bot תתאמו", chat_id=GEN, message_id="919"))
    assert sent == [("🔄 ⏳", "919")]


def test_reply_to_bot_unchanged(tmp_path, monkeypatch):
    sent = _no_live(monkeypatch, tmp_path, owner="trading")
    out = auto_ack.handle(ch("כן", chat_id=GEN, message_id="920", reply_to_message_id="900"))
    assert sent == [] and "reason=reply_to_bot BOT=trading" in out[0]


def test_bot_owned_helper_for_typing_hook(monkeypatch):
    monkeypatch.setattr(auto_ack, "bot_reply_owner", lambda attrs: None)
    a, c = auto_ack.parse_channels(ch("@kai__invest_bot מה עם NVDA", chat_id=GEN))[0]
    assert auto_ack.bot_owned(a, c) == ("direct_bot_tag", "trading")
    a, c = auto_ack.parse_channels(ch("מה עם NVDA", chat_id=GEN))[0]
    assert auto_ack.bot_owned(a, c) is None


def test_typing_hook_opens_no_lease_for_bot_tag(monkeypatch):
    import io
    import typing_lease
    import typing_session_start
    opened = []
    monkeypatch.setattr(typing_lease, "open_lease", lambda *a, **k: opened.append((a, k)))
    monkeypatch.setattr(typing_session_start, "_tlog", lambda *a: None)
    monkeypatch.setattr(auto_ack, "bot_reply_owner", lambda attrs: None)
    for text, expect in (("@kai__miluim_bot תבדוק", 0), ("תבדוק את הלוז", 1)):
        opened.clear()
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"prompt": ch(text, chat_id=GEN)})))
        typing_session_start.main()
        assert len(opened) == expect, text
