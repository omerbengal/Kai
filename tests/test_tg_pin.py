"""tg_pin.py per chat (07/11/2026, Omer msg 605 in the bots group): the registry is keyed by chat (and by
domain bot), a group ACK never re-pins a private one, old list registries still load, stale pins are swept."""
import json
import sys

sys.path.insert(0, "D:/Kai/scripts")
import tg_pin as P  # noqa: E402

GEN = "-1004297864522"


def setup(monkeypatch, tmp_path, initial=None):
    reg = tmp_path / "pinned.json"
    if initial is not None:
        reg.write_text(json.dumps(initial), encoding="utf-8")
    monkeypatch.setattr(P, "REGISTRY", str(reg))
    calls = []
    monkeypatch.setattr(P, "_api", lambda m, p, bot=None: calls.append((m, p["chat_id"], p["message_id"], bot)) or True)
    return calls


def test_group_pin_uses_group_chat(monkeypatch, tmp_path):
    calls = setup(monkeypatch, tmp_path)
    P.pin_ack(606, GEN)
    assert calls == [("pinChatMessage", GEN, 606, None)]
    assert [x[0] for x in P._read_registry()[GEN]] == [606]


def test_unpin_repins_only_same_chat(monkeypatch, tmp_path):
    calls = setup(monkeypatch, tmp_path)
    P.pin_ack(10, "${KAI_TELEGRAM_CHAT_ID}")
    P.pin_ack(20, GEN)
    P.pin_ack(30, GEN)
    calls.clear()
    P.unpin_ack(30, GEN)
    assert calls == [("unpinChatMessage", GEN, 30, None), ("pinChatMessage", GEN, 20, None)]
    calls.clear()
    P.unpin_ack(20, GEN)  # the private 10 must NOT be re-pinned into the group
    assert calls == [("unpinChatMessage", GEN, 20, None)]


def test_unpin_not_pinned_is_noop(monkeypatch, tmp_path):
    calls = setup(monkeypatch, tmp_path)
    P.pin_ack(5, GEN)
    calls.clear()
    P.unpin_ack(999, GEN)  # never pinned: one harmless unpin call, no re-pin of 5
    assert calls == [("unpinChatMessage", GEN, 999, None)]


def test_legacy_list_registry_is_private(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, initial=[11, 12])
    assert [x[0] for x in P._read_registry()["${KAI_TELEGRAM_CHAT_ID}"]] == [11, 12]


def test_domain_bot_key_and_token(monkeypatch, tmp_path):
    calls = setup(monkeypatch, tmp_path)
    P.pin_ack(7, GEN, bot="miluim")
    P.pin_ack(8, GEN)  # main bot, same chat, separate key
    assert set(P._read_registry()) == {"miluim@" + GEN, GEN}
    calls.clear()
    P.unpin_ack(7, GEN, bot="miluim")
    assert calls == [("unpinChatMessage", GEN, 7, "miluim")]


def test_sweep_stale(monkeypatch, tmp_path):
    calls = setup(monkeypatch, tmp_path, initial={GEN: [[1, 1000.0], [2, 9000.0]], "miluim@" + GEN: [[3, 0]]})
    n = P.sweep_stale(max_age=3600, now=10000.0)
    assert n == 2
    assert sorted(c[2] for c in calls) == [1, 3]
    assert P._read_registry() == {GEN: [[2, 9000.0]]}


def test_clear_main_keeps_domain_bots(monkeypatch, tmp_path):
    calls = setup(monkeypatch, tmp_path, initial={GEN: [[1, 5.0]], "${KAI_TELEGRAM_CHAT_ID}": [[2, 5.0]], "trading@" + GEN: [[3, 5.0]]})
    assert P.clear_main() == 2
    assert sorted(c[2] for c in calls) == [1, 2]
    assert set(P._read_registry()) == {"trading@" + GEN}


def test_api_never_raises_on_missing_bot_token(monkeypatch):
    def boom(bot):
        raise SystemExit("no token")
    monkeypatch.setattr(P, "_token", boom)
    assert P._api("pinChatMessage", {"chat_id": GEN, "message_id": 1}, "nobot") is False
