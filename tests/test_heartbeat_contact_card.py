"""heartbeat.send_telegram contact-card path: retry + logged fallback (bug 20/10/2026 11:20 UTC:
one sendContact hit the 10s timeout and the reminder silently arrived as plain text)."""
import os, sys, sqlite3, socket
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import heartbeat
import phone_links


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, telegram_message_id INTEGER, direction TEXT, "
                 "text TEXT, reply_to_message_id INTEGER, ts TEXT, reminder_id INTEGER, task_id INTEGER)")
    conn.execute("CREATE TABLE action_log (id INTEGER PRIMARY KEY, agent TEXT, action TEXT, details TEXT)")
    return conn


def fake_api(fail_contacts):
    calls = []
    state = {"left": fail_contacts, "id": 500}

    def api(method, payload, timeout=10):
        calls.append(method)
        if method == "sendContact" and state["left"] > 0:
            state["left"] -= 1
            raise socket.timeout("timed out")
        state["id"] += 1
        return {"ok": True, "result": {"message_id": state["id"]}}
    return api, calls


def run(monkeypatch, fails):
    monkeypatch.setattr(phone_links, "_load_config", lambda: {"contact_mode": "card"})
    api, calls = fake_api(fails)
    monkeypatch.setattr(heartbeat, "_tg_api", api)
    conn = make_conn()
    mid = heartbeat.send_telegram("⏰ להתקשר למוביט - *5090", conn=conn, alert=True, link=("reminder_id", 251))
    logs = [r[0] for r in conn.execute("SELECT details FROM action_log WHERE action='contact_card'")]
    row = conn.execute("SELECT text, reminder_id FROM messages WHERE telegram_message_id=?", (mid,)).fetchone()
    return calls, logs, row


def test_card_first_try(monkeypatch):
    calls, logs, row = run(monkeypatch, 0)
    assert calls == ["sendContact"] and logs == []
    assert row == ("⏰ להתקשר למוביט - *5090", 251)  # 👍 on the card finds reminder 251


def test_one_timeout_is_retried_as_card(monkeypatch):
    calls, logs, row = run(monkeypatch, 1)
    assert calls == ["sendContact", "sendContact"]  # never fell back to sendMessage
    assert len(logs) == 1 and "retry" in logs[0] and "timeout" in logs[0]
    assert row[1] == 251


def test_two_failures_fall_back_to_text_and_log_why(monkeypatch):
    calls, logs, row = run(monkeypatch, 2)
    assert calls == ["sendContact", "sendContact", "sendMessage"]
    assert len(logs) == 1 and "sent as text" in logs[0] and "timeout" in logs[0]
    assert row[1] == 251


def test_heartbeat_prefers_ipv4():
    assert getattr(socket, "_kai_ipv4_first", False)
