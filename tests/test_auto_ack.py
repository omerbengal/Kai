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
