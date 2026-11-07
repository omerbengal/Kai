"""dispatch_start.py in the bots group's General topic (06/11/2026, Omer msg 22689): group message ids collide
with private ones, so a group ACK gets no `messages` row and no ack_log row (stale_ack.py edits ack_log ids
in the PRIVATE chat and would hit an unrelated private message)."""
import sys

sys.path.insert(0, "D:/Kai/scripts")
import dispatch_start as D  # noqa: E402

GEN = "-1004297864522"


def run(monkeypatch, argv):
    calls = {"save": [], "log": [], "edit": [], "send": []}
    monkeypatch.setattr(D.tg_silent_send, "send", lambda c, t, r: calls["send"].append(c) or 321)
    monkeypatch.setattr(D.tg_silent_send, "save", lambda m, t: calls["save"].append(m))
    monkeypatch.setattr(D, "log_ack", lambda m, a, t: calls["log"].append(m))
    monkeypatch.setattr(D, "edit_text", lambda c, m, t: calls["edit"].append((c, m)))
    monkeypatch.setattr(sys, "argv", ["dispatch_start.py"] + argv + ["--no-context"])
    D.main()
    return calls


def test_group_send_no_rows(monkeypatch):
    c = run(monkeypatch, ["--chat_id", GEN, "--reply_to", "5", "--title", "x"])
    assert c["send"] == [GEN] and c["save"] == [] and c["log"] == []


def test_group_auto_ack_edit_no_rows(monkeypatch):
    c = run(monkeypatch, ["--chat_id", GEN, "--ack_id", "77", "--title", "x"])
    assert c["edit"] == [(GEN, 77)] and c["log"] == []


def test_private_keeps_rows(monkeypatch):
    c = run(monkeypatch, ["--reply_to", "5", "--title", "x"])
    assert c["save"] == [321] and c["log"] == [321]


def test_group_pin_goes_to_group(monkeypatch):
    """07/11/2026 (Omer msg 605): --pin in the group pins there (it used to be private-only)."""
    pins = []
    monkeypatch.setattr(D.tg_pin, "pin_ack", lambda m, c: pins.append((m, c)))
    run(monkeypatch, ["--chat_id", GEN, "--ack_id", "606", "--title", "x", "--pin"])
    assert pins == [(606, GEN)]


def test_private_pin_unchanged(monkeypatch):
    pins = []
    monkeypatch.setattr(D.tg_pin, "pin_ack", lambda m, c: pins.append((m, c)))
    run(monkeypatch, ["--reply_to", "5", "--title", "x", "--pin"])
    assert pins == [(321, "${KAI_TELEGRAM_CHAT_ID}")]
