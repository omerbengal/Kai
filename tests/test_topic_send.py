"""scripts/topic_send.py - scheduled domain alerts go to the domain bot's topic (06/11/2026, Omer msg 22614:
"התראות המסחר צריך לבוט המסחר"). the alert scripts send through it."""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, "D:/Kai/scripts")
sys.path.insert(0, "D:/Kai/bots")
import topic_send as ts  # noqa: E402

GROUP = "-1004297864522"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    st = tmp_path / "st"
    st.mkdir()
    (st / ".env").write_text("TELEGRAM_BOT_TOKEN=111:trading\n", encoding="utf-8")
    c = {"home": str(tmp_path), "state_dir": str(st), "group_id": GROUP, "topics": ["117"]}
    monkeypatch.setattr(ts.kai_bots, "load_config", lambda *a, **k: {"trading": c})
    import tg_log
    monkeypatch.setattr(tg_log, "save_outgoing", lambda *a, **k: True)  # never write the real kai.db
    return c


class Net:
    def __init__(self, fail_tokens=()):
        self.calls, self.fail = [], set(fail_tokens)

    def __call__(self, token, payload):
        self.calls.append((token, payload))
        if token in self.fail:
            raise RuntimeError("HTTP 400")
        return 4000 + len(self.calls)


def test_goes_to_the_trading_topic_with_the_bot_token(cfg):
    net = Net()
    mid, where = ts.send("trading", "‏התראה", post=net)
    tok, p = net.calls[0]
    assert tok == "111:trading"
    assert p["chat_id"] == GROUP and p["message_thread_id"] == 117 and p["text"] == "‏התראה"
    assert (mid, where) == (4001, "topic")
    rows = sqlite3.connect(os.path.join(cfg["home"], "bot.db")).execute(
        "SELECT bot, chat_id, thread_id, telegram_message_id, direction FROM bot_messages").fetchall()
    assert rows == [("trading", GROUP, "117", 4001, "outgoing")]


def test_reply_to_is_kept(cfg):
    net = Net()
    ts.send("trading", "x", reply_to=55, post=net)
    assert net.calls[0][1]["reply_parameters"] == {"message_id": 55, "allow_sending_without_reply": True}


def test_bot_send_failure_falls_back_to_private_chat(cfg):
    net = Net(fail_tokens={"111:trading"})
    mid, where = ts.send("trading", "‏התראה", post=net)
    assert where == "private" and mid == 4002
    tok, p = net.calls[1]
    assert tok == ts.main_token() and p["chat_id"] == ts.OMER_CHAT and "message_thread_id" not in p


def test_unknown_domain_or_missing_token_uses_private(cfg, tmp_path):
    net = Net()
    assert ts.send("nope", "x", post=net)[1] == "private"
    os.remove(os.path.join(cfg["state_dir"], ".env"))
    assert ts.send("trading", "x", post=net)[1] == "private"


def test_everything_failing_returns_none(cfg):
    net = Net(fail_tokens={"111:trading", ts.main_token()})
    assert ts.send("trading", "x", post=net) == (None, None)


def test_keyboard_is_sent_as_reply_markup(cfg):
    net = Net()
    kb = [[{"text": "✔️ טופל", "callback_data": "cb_done:1"}]]
    ts.send("trading", "x", keyboard=kb, post=net)
    assert net.calls[0][1]["reply_markup"] == {"inline_keyboard": kb}


def test_private_reply_to_is_used_only_in_the_private_fallback(cfg):
    # the entry message lives in the private chat: its id means nothing (or another message) in the topic
    net = Net()
    ts.send("trading", "x", private_reply_to=18594, post=net)
    assert "reply_parameters" not in net.calls[0][1]
    net = Net(fail_tokens={"111:trading"})
    ts.send("trading", "x", private_reply_to=18594, post=net)
    assert net.calls[1][1]["reply_parameters"] == {"message_id": 18594, "allow_sending_without_reply": True}


def test_position_scripts_send_through_the_trading_topic():
    for f in ("swing_watch.py", "position_watch.py"):
        src = open(os.path.join("D:/Kai/scripts", f), encoding="utf-8").read()
        assert "/sendMessage" not in src, f
        assert "topic_send.send(\"trading\"" in src, f
        assert "private_reply_to=" in src, f


def _capture(monkeypatch, mod):
    calls = []

    def fake(domain, text, reply_to=None, keyboard=None, private_reply_to=None):
        calls.append(dict(domain=domain, text=text, reply_to=reply_to, keyboard=keyboard,
                          private_reply_to=private_reply_to))
        return 777, "topic"
    monkeypatch.setattr(ts, "send", fake)
    monkeypatch.setattr(mod, "DB_PATH", ":memory:")
    monkeypatch.setattr(mod, "LOG", os.devnull)
    return calls


def test_swing_watch_alert_with_buttons_goes_to_the_topic(monkeypatch):
    import swing_watch
    calls = _capture(monkeypatch, swing_watch)
    kb = swing_watch.close_buttons("GTM")
    assert swing_watch.send(["⚠️ GTM ליד הסטופ"], 18594, kb) == 777
    c = calls[0]
    assert c["domain"] == "trading" and c["keyboard"] == kb and c["reply_to"] is None
    assert c["private_reply_to"] == 18594 and "GTM" in c["text"]


def test_position_watch_alert_goes_to_the_topic(monkeypatch):
    import position_watch
    calls = _capture(monkeypatch, position_watch)
    assert position_watch.send("🎯 PEGA נגעה ביעד", "17942") == 777
    c = calls[0]
    assert c["domain"] == "trading" and c["reply_to"] is None and c["private_reply_to"] == "17942"


def test_pos_callbacks_belong_to_the_trading_bot_with_a_private_fallback():
    dom = open("D:/Kai/bots/_domains/trading.md", encoding="utf-8").read()
    assert "pos_sold" in dom and "swing_watch.py --close" in dom
    lst = open("D:/Kai/agents/listener.md", encoding="utf-8").read()
    i = lst.index("