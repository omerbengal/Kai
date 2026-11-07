"""Domain bots (07/11/2026): every ACK delete unpins first, and pin/unpin go through tg_pin with the bot's key."""
import sys

sys.path.insert(0, "D:/Kai/bots")
sys.path.insert(0, "D:/Kai/scripts")
import bot_tg  # noqa: E402
import tg_pin  # noqa: E402

GEN = "-1004297864522"


class _NoConn:
    def execute(self, *a):
        return self

    def commit(self):
        pass

    def close(self):
        pass


def test_drop_ack_unpins_before_delete(monkeypatch):
    order = []
    monkeypatch.setattr(bot_tg, "_pin", lambda cmd, b, c, m: order.append((cmd, b, c, m)))
    monkeypatch.setattr(bot_tg, "api", lambda b, method, **p: order.append((method, p["message_id"])))
    monkeypatch.setattr(bot_tg, "_typing", lambda *a: None)
    monkeypatch.setattr(bot_tg, "_conn", lambda **k: _NoConn())
    bot_tg.drop_ack("miluim", GEN, 55)
    assert order == [("unpin", "miluim", GEN, 55), ("deleteMessage", 55)]


def test_pin_helper_uses_bot_key(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tg_pin, "REGISTRY", str(tmp_path / "r.json"))
    monkeypatch.setattr(tg_pin, "_api", lambda m, p, bot=None: calls.append((m, p["message_id"], bot)) or True)
    bot_tg._pin("pin", "trading", GEN, "77")
    assert calls == [("pinChatMessage", 77, "trading")]
    assert set(tg_pin._read_registry()) == {"trading@" + GEN}
