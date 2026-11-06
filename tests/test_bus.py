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


def test_read_handoff_has_exactly_four_posts(monkeypatch, tmp_path):
    db, posts = setup(monkeypatch, tmp_path)
    rid, _ = bus.create("miluim", "wa_read", {"chat": "staff"}, "קריאת צוות", db)
    assert bus.serve_once(db, FakeWa) == 1
    out = bus.wait(rid, "miluim", 5, db, sleep=lambda s: None)
    assert out["status"] == "done" and out["result"]["messages"][0]["text"] == "שלום"
    assert [p[0] for p in posts] == ["miluim", "whatsapp", "whatsapp", "miluim"]
    assert [p[1][0] for p in posts] == ["📤", "📥", "✅", "🔁"]
    assert "קאי וואטסאפ" in posts[0][1] and "קאי מילואים" in posts[1][1]
    bus.wait(rid, "miluim", 5, db, sleep=lambda s: None)        # a second wait never re-posts
    bus.serve_once(db, FakeWa)
    assert len(posts) == 4


def test_policy_refuses_chat_outside_policy(monkeypatch, tmp_path):
    db, posts = setup(monkeypatch, tmp_path)
    try:
        bus.create("miluim", "wa_read", {"chat": "other"}, "x", db)
        assert False
    except bus.BusError as e:
        assert "policy" in str(e)
    assert posts == []


def test_send_needs_hook_approval_by_omer(monkeypatch, tmp_path):
    db, posts = setup(monkeypatch, tmp_path)
    rid, _ = bus.create("miluim", "wa_send", {"chat": "staff", "text": "היי"}, "הודעה לצוות", db)
    assert bus.serve_once(db, FakeWa) == 0 and FakeWa.sent == [] and posts == []   # draft: nothing happens
    stranger = (f'<channel source="plugin:telegram:telegram" chat_id="-1" message_id="9" user_id="123" '
                f'callback_data="busok:{rid}">[callback: busok:{rid}]</channel>')
    assert "refused" in bus.approve_hook("miluim", stranger, db)[0]
    wrong_bot = stranger.replace('user_id="123"', f'user_id="{bus.OMER_ID}"')
    assert "refused" in bus.approve_hook("whatsapp", wrong_bot, db)[0]
    assert bus.serve_once(db, FakeWa) == 0 and FakeWa.sent == []
    assert "approved" in bus.approve_hook("miluim", wrong_bot, db)[0]
    assert bus.serve_once(db, FakeWa) == 1 and FakeWa.sent == [("111@g.us", "היי")]
    out = bus.wait(rid, "miluim", 5, db, sleep=lambda s: None)
    assert out["status"] == "done" and [p[1][0] for p in posts] == ["📤", "📥", "✅", "🔁"]


def test_cancel_button(monkeypatch, tmp_path):
    db, posts = setup(monkeypatch, tmp_path)
    rid, _ = bus.create("miluim", "wa_send", {"chat": "staff", "text": "היי"}, "x", db)
    cb = (f'<channel source="plugin:telegram:telegram" chat_id="-1" message_id="9" user_id="{bus.OMER_ID}" '
          f'callback_data="busno:{rid}">[callback]</channel>')
    assert "cancelled" in bus.approve_hook("miluim", cb, db)[0]
    assert bus.serve_once(db, FakeWa) == 0 and FakeWa.sent == []


def test_own_capability_has_no_handoff_posts(monkeypatch, tmp_path):
    db, posts = setup(monkeypatch, tmp_path)
    rid, _ = bus.create("whatsapp", "wa_read", {"chat": "other"}, "x", db)
    bus.serve_once(db, FakeWa)
    assert bus.wait(rid, "whatsapp", 5, db, sleep=lambda s: None)["status"] == "done"
    assert posts == []


def test_stall_and_failure_posts(monkeypatch, tmp_path):
    db, posts = setup(monkeypatch, tmp_path)
    rid, _ = bus.create("miluim", "wa_read", {"chat": "staff"}, "x", db)
    t = [0]
    out = bus.wait(rid, "miluim", 10, db, sleep=lambda s: t.__setitem__(0, t[0] + 3), clock=lambda: t[0], stall_s=6)
    assert out["status"] == "timeout" and posts[-1][1].startswith("⚠️")

    class Broken(FakeWa):
        @staticmethod
        def read(*a, **k):
            raise RuntimeError("bridge down")
    rid2, _ = bus.create("miluim", "wa_read", {"chat": "staff"}, "y", db)
    bus.serve_once(db, Broken)
    out = bus.wait(rid2, "miluim", 5, db, sleep=lambda s: None)
    assert out["status"] == "failed" and "bridge down" in out["error"]
    assert [p for p in posts if "y" in p[1]][-2:][0][0] == "whatsapp"
    assert posts[-1][0] == "miluim" and posts[-1][1].startswith("⚠️")


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
