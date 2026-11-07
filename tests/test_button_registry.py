"""Open-buttons registry + "which question does this answer" (scripts/button_registry.py, 07/11/2026)."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import button_registry as br  # noqa: E402

CHAT = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
YN = [[{"text": "✅ כן, תרסטרט", "callback_data": "rs_yes"}, {"text": "❌ לא עכשיו", "callback_data": "rs_no"}]]
PICK = [[{"text": "כיוון 1", "callback_data": "d1"}, {"text": "כיוון 3", "callback_data": "d3"}]]


class FakeApi:
    def __init__(self, markup_error=None):
        self.calls = []
        self.markup_error = markup_error

    def __call__(self, method, **params):
        self.calls.append((method, params))
        if method == "editMessageReplyMarkup" and self.markup_error:
            raise br.TgError(self.markup_error)
        return True

    def texts(self):
        return [p["text"] for m, p in self.calls if m == "editMessageText"]


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "t.db")


def chan(text, msg_id="900", reply_to=None, callback=None, user_id=CHAT, chat=CHAT):
    attrs = f'source="telegram" chat_id="{chat}" message_id="{msg_id}" user_id="{user_id}"'
    if reply_to:
        attrs += f' reply_to_message_id="{reply_to}"'
    if callback:
        attrs += f' callback_data="{callback}"'
    return f"<channel {attrs}>{text}</channel>"


# ---------------------------------------------------------------- registry
def test_register_from_reply_tool_and_list(db):
    ti = {"chat_id": CHAT, "text": "‏לעשות ריסטרט?", "inline_keyboard": YN}
    assert br.on_outgoing(ti, '{"content": "sent (id: 571)"}', db=db) == 571
    rows = br.list_open(CHAT, db=db)
    assert [r["message_id"] for r in rows] == [571]
    assert rows[0]["text"] == "‏לעשות ריסטרט?"


def test_reply_without_buttons_is_not_registered(db):
    assert br.on_outgoing({"chat_id": CHAT, "text": "hi"}, "sent (id: 5)", db=db) is None
    assert br.list_open(CHAT, db=db) == []


def test_keyboard_sits_on_last_text_chunk_not_on_files(db):
    ti = {"chat_id": CHAT, "text": "x", "inline_keyboard": YN, "files": ["a.png"]}
    assert br.on_outgoing(ti, "sent 2 parts (ids: 10, 11)", db=db) == 10
    ti2 = {"chat_id": CHAT, "text": "long", "inline_keyboard": YN}
    assert br.on_outgoing(ti2, "sent 2 parts (ids: 20, 21)", db=db) == 21
    assert br.get(CHAT, 21, db=db)["text"] is None  # the last chunk's text is unknown


def test_old_entries_are_not_listed(db):
    br.register(CHAT, 1, "q", YN, db=db)
    later = datetime.now(timezone.utc) + timedelta(days=8)
    assert br.list_open(CHAT, db=db, now=later) == []


# ---------------------------------------------------------------- labels
def test_label_core_and_polarity():
    assert br.label_core("✅ כן, תרסטרט") == "כן, תרסטרט"
    assert br.label_for(YN, "rs_no") == "לא עכשיו"
    assert br.polarity("✅ כן, תרסטרט") == "yes"
    assert br.polarity("❌ לא עכשיו") == "no"
    assert br.polarity("כיוון 3") is None


# ---------------------------------------------------------------- close
def test_close_removes_buttons_and_appends_chosen_line(db):
    br.register(CHAT, 7, "‏לעשות ריסטרט?", YN, db=db)
    api = FakeApi()
    assert br.close(CHAT, 7, "כן, תרסטרט", api=api, db=db) == "closed"
    assert api.calls[0][0] == "editMessageReplyMarkup"
    assert api.texts() == ["‏לעשות ריסטרט?\n\n‏✅ נבחר: כן, תרסטרט"]
    assert br.get(CHAT, 7, db=db)["status"] == "closed"
    assert br.close(CHAT, 7, "x", api=FakeApi(), db=db) == "already"


def test_close_leaves_text_alone_when_buttons_already_gone(db):
    """A worker already replaced the question: never rewrite it back from the registry."""
    br.register(CHAT, 8, "q", YN, db=db)
    api = FakeApi(markup_error='{"description":"Bad Request: message is not modified"}')
    assert br.close(CHAT, 8, "כן", api=api, db=db) == "gone"
    assert api.texts() == []
    assert br.get(CHAT, 8, db=db)["status"] == "gone"


def test_close_markdownv2_escapes_the_new_line(db):
    br.register(CHAT, 9, "*q*", YN, parse_mode="markdownv2", db=db)
    api = FakeApi()
    br.close(CHAT, 9, "כן.", api=api, db=db)
    m, p = [c for c in api.calls if c[0] == "editMessageText"][0]
    assert p["parse_mode"] == "MarkdownV2" and p["text"].endswith("כן\\.")


# ---------------------------------------------------------------- match
def test_reply_with_a_button_label_is_high(db):
    br.register(CHAT, 50, "q", PICK, db=db)
    m = br.match(CHAT, "כיוון 3", reply_to=50, db=db)
    assert m == {"status": "high", "message_id": 50, "chosen": "כיוון 3"}


def test_reply_with_free_text_goes_to_dispatcher(db):
    br.register(CHAT, 50, "q", PICK, db=db)
    m = br.match(CHAT, "מה ההבדל ביניהם?", reply_to=50, db=db)
    assert m["status"] == "reply" and m["chosen"] is None


def test_plain_yes_answers_only_the_newest_question(db):
    br.register(CHAT, 60, "q1", YN, db=db)
    br.register(CHAT, 61, "q2", PICK, db=db)  # newest has no yes/no buttons
    assert br.match(CHAT, "כן", db=db)["status"] == "none"
    br.register(CHAT, 62, "q3", YN, db=db)
    m = br.match(CHAT, "כן", db=db)
    assert m["status"] == "ambiguous"  # two yes/no questions open: the dispatcher decides by content


def test_plain_yes_with_one_open_question_is_high(db):
    br.register(CHAT, 70, "q", YN, db=db)
    assert br.match(CHAT, "סבבה", db=db) == {"status": "high", "message_id": 70, "chosen": "כן, תרסטרט"}


def test_plain_exact_label_is_high_even_if_not_newest(db):
    br.register(CHAT, 80, "q", PICK, db=db)
    br.register(CHAT, 81, "q2", YN, db=db)
    assert br.match(CHAT, "כיוון 1", db=db)["message_id"] == 80


def test_unrelated_text_lists_candidates(db):
    br.register(CHAT, 90, "q", PICK, db=db)
    m = br.match(CHAT, "תזכיר לי מחר לקנות חלב", db=db)
    assert m["status"] == "none" and m["candidates"][0]["labels"] == ["כיוון 1", "כיוון 3"]


def test_other_chat_is_not_matched(db):
    br.register("-1004297864522", 95, "q", YN, db=db)
    assert br.match(CHAT, "כן", db=db)["status"] == "none"


# ---------------------------------------------------------------- incoming hook
def test_press_closes_with_the_pressed_label(db):
    br.register(CHAT, 100, "‏לעשות ריסטרט?", YN, db=db)
    api = FakeApi()
    r = br.on_incoming(chan("[callback: rs_yes] על: ...", msg_id="100", callback="rs_yes"), api=api, db=db)
    assert r == "closed"
    assert [m for m, _ in api.calls] == ["editMessageText"]  # the plugin already removed the buttons
    assert api.texts() == ["‏לעשות ריסטרט?\n\n‏✅ נבחר: כן, תרסטרט"]


def test_press_on_unregistered_message_does_nothing(db):
    api = FakeApi()
    assert br.on_incoming(chan("[callback: x]", msg_id="5", callback="x"), api=api, db=db) is None
    assert api.calls == []


def test_text_reply_naming_a_button_closes_it(db):
    br.register(CHAT, 110, "q", PICK, db=db)
    api = FakeApi()
    assert br.on_incoming(chan("כיוון 3", reply_to="110"), api=api, db=db) == "closed"
    assert api.texts() == ["q\n\n‏✅ נבחר: כיוון 3"]


def test_free_text_is_left_to_the_dispatcher(db):
    br.register(CHAT, 120, "q", PICK, db=db)
    api = FakeApi()
    assert br.on_incoming(chan("תלוי, מה יותר זול?", reply_to="120"), api=api, db=db) is None
    assert api.calls == [] and br.get(CHAT, 120, db=db)["status"] == "open"


def test_messages_from_other_bots_are_ignored(db):
    br.register(CHAT, 130, "q", YN, db=db)
    api = FakeApi()
    assert br.on_incoming(chan("כן", user_id="12345"), api=api, db=db) is None
    assert api.calls == []


def test_chosen_prefix_is_rtl():
    assert br.chosen_line("x").startswith("‏✅ נבחר: ")
