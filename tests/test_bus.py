"""bots/bus.py - the request bus between Kai bots (multi-bot stage 2, 05/11/2026)."""
import json
import os
import sys
import tempfile

sys.path.insert(0, "D:/Kai/bots")
sys.path.insert(0, "D:/Kai/scripts")
import bus  # noqa: E402
import kai_bots  # noqa: E402
import wa  # noqa: E402

CFG = {"bots": {
    "miluim": {"title": "קאי מילואים", "home": "x", "caps": [], "group_id": "-1", "topics": ["2"],
               "wa_policy": {"read": ["staff"], "send": ["staff"]}},
    "whatsapp": {"title": "קאי וואטסאפ", "home": "y", "caps": ["whatsapp", "contacts_owner"], "group_id": "-1",
                 "topics": ["121"], "wa_policy": {"read": ["*"], "send": ["*"]}},
}, "wa_aliases": {"staff": "111@g.us", "other": "222@g.us"}}


class FakeWa:
    sent = []

    @staticmethod
    def read(chat, limit=50, since_hours=None):
        return {"chat": "צוות", "messages": [{"from": "דנה", "text": "שלום"}]}

    @staticmethod
    def search(q, chat=None, limit=30):
        return {"count": 0}

    @staticmethod
    def send(chat, text):
        FakeWa.sent.append((chat, text))
        return {"sent": True}


def setup(monkeypatch, tmp_path):
    cfgp = tmp_path / "bots.json"
    cfgp.write_text(json.dumps(CFG, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(kai_bots, "CONFIG", str(cfgp))
    monkeypatch.setattr(bus, "bots", lambda config_path=None: kai_bots.load_config(str(cfgp)))
    monkeypatch.setattr(wa, "aliases", lambda config_path=None: CFG["wa_aliases"])
    monkeypatch.setattr(wa, "resolve_chat", lambda chat, *a, **k: (CFG["wa_aliases"].get(chat, chat), "שם " + chat))
    posts = []
    monkeypatch.setattr(bus, "POST", lambda b, text, silent=True: posts.append((b, text.lstrip("\u200f"))))
    monkeypatch.delenv("KAI_BOT", raising=False)
    FakeWa.sent = []
    return str(tmp_path / "bus.db"), posts


def test_domain_bot_is_refused_whatsapp_goes_through_general(monkeypatch, tmp_path):
    """06/11/2026 (Omer msgs 358 / 361): no hidden bus for other bots. A bot without the whatsapp cap asks
    the WhatsApp bot by tagging it in General (bot_tg.py ask); the bus refuses it, reads and sends alike."""
    db, posts = setup(monkeypatch, tmp_path)
    for kind, payload in (("wa_read", {"chat": "staff"}), ("wa_search", {"query": "x", "chat": "staff"}),
                          ("wa_send", {"chat": "staff", "text": "היי"})):
        with __import__("pytest").raises(bus.BusError) as e:
            bus.create("miluim", kind, payload, "x", db)
        assert "General" in str(e.value) and "ask" in str(e.value)
    assert posts == [] and FakeWa.sent == []


def test_send_needs_hook_approval_by_omer(monkeypatch, tmp_path):
    db, posts = setup(monkeypatch, tmp_path)
    rid, _ = bus.create("whatsapp", "wa_send", {"chat": "staff", "text": "היי"}, "הודעה לצוות", db)
    assert bus.serve_once(db, FakeWa) == 0 and FakeWa.sent == []   # draft: nothing happens
    stranger = (f'<channel source="plugin:telegram:telegram" chat_id="-1" message_id="9" user_id="123" '
                f'callback_data="busok:{rid}">[callback: busok:{rid}]</channel>')
    assert "refused" in bus.approve_hook("whatsapp", stranger, db)[0]
    omer = stranger.replace('user_id="123"', f'user_id="{bus.OMER_ID}"')
    assert "refused" in bus.approve_hook("miluim", omer, db)[0]       # not miluim's request
    assert bus.serve_once(db, FakeWa) == 0 and FakeWa.sent == []
    assert "approved" in bus.approve_hook("whatsapp", omer, db)[0]
    assert bus.serve_once(db, FakeWa) == 1 and FakeWa.sent == [("111@g.us", "היי")]
    out = bus.wait(rid, "whatsapp", 5, db, sleep=lambda s: None)
    assert out["status"] == "done" and posts == []


def test_cancel_button(monkeypatch, tmp_path):
    db, posts = setup(monkeypatch, tmp_path)
    rid, _ = bus.create("whatsapp", "wa_send", {"chat": "staff", "text": "היי"}, "x", db)
    cb = (f'<channel source="plugin:telegram:telegram" chat_id="-1" message_id="9" user_id="{bus.OMER_ID}" '
          f'callback_data="busno:{rid}">[callback]</channel>')
    assert "cancelled" in bus.approve_hook("whatsapp", cb, db)[0]
    assert bus.serve_once(db, FakeWa) == 0 and FakeWa.sent == []


def test_own_capability_has_no_handoff_posts(monkeypatch, tmp_path):
    db, posts = setup(monkeypatch, tmp_path)
    rid, _ = bus.create("whatsapp", "wa_read", {"chat": "other"}, "x", db)
    bus.serve_once(db, FakeWa)
    assert bus.wait(rid, "whatsapp", 5, db, sleep=lambda s: None)["status"] == "done"
    assert posts == []


def test_failure_is_reported(monkeypatch, tmp_path):
    db, posts = setup(monkeypatch, tmp_path)

    class Broken(FakeWa):
        @staticmethod
        def read(*a, **k):
            raise RuntimeError("bridge down")
    rid, _ = bus.create("whatsapp", "wa_read", {"chat": "staff"}, "y", db)
    bus.serve_once(db, Broken)
    out = bus.wait(rid, "whatsapp", 5, db, sleep=lambda s: None)
    assert out["status"] == "failed" and "bridge down" in out["error"]


def test_caller_identity_from_env(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setenv("KAI_BOT", "miluim")
    try:
        bus.check_caller("whatsapp")
        assert False
    except bus.BusError:
        pass
    bus.check_caller("miluim")


def test_signature_added_once():
    assert wa.with_signature("היי").endswith(wa.SIGNATURE)
    assert wa.with_signature(wa.with_signature("היי")).count(wa.SIGNATURE) == 1
