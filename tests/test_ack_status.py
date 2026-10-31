"""ACK status text: "מייצר agent" only while one is really being spawned (Omer msg 21171, 31/10/2026)."""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "D:/Kai/scripts")
import dispatch_start  # noqa: E402
import stale_ack  # noqa: E402

NOW = datetime(2026, 10, 31, 20, 0, 0, tzinfo=timezone.utc)


def ts(seconds_ago):
    return (NOW - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%d %H:%M:%S")


def run_main(monkeypatch, argv):
    calls = {"edit": [], "log": [], "pin": []}
    monkeypatch.setattr(dispatch_start, "edit_text", lambda c, m, t: calls["edit"].append((m, t)))
    monkeypatch.setattr(dispatch_start, "log_ack", lambda a, act, t: calls["log"].append((a, act, t)))
    monkeypatch.setattr(dispatch_start.tg_pin, "pin_ack", lambda m, c: calls["pin"].append(m))
    monkeypatch.setattr(sys, "argv", ["dispatch_start.py"] + argv)
    dispatch_start.main()
    return calls


def test_title_says_spawning(monkeypatch):
    c = run_main(monkeypatch, ["--ack_id", "5", "--title", "בודק", "--pin", "--no-context"])
    assert c["edit"] == [(5, "🔄 בודק — ⏳ מייצר agent...")]
    assert c["pin"] == [5]


def test_queued_never_says_spawning(monkeypatch):
    c = run_main(monkeypatch, ["--ack_id", "5", "--title", "בודק", "--queued", "--pin", "--no-context"])
    text = c["edit"][0][1]
    assert text == "🔄 בודק — 📋 בתור אצל ה-agent שכבר עובד על זה"
    assert "מייצר" not in text
    assert c["log"] == [(5, "sent", text)]
    assert c["pin"] == [5]


def make_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE ack_log (id INTEGER PRIMARY KEY, ack_msg_id INT, action TEXT, text TEXT, ts TEXT)")
    conn.executemany("INSERT INTO ack_log (ack_msg_id, action, text, ts) VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


SPAWN = "🔄 בודק — ⏳ מייצר agent..."


def run_check(tmp_path, rows):
    db = str(tmp_path / "k.db")
    make_db(db, rows)
    root = tmp_path / "projects"
    (root / "s" / "subagents").mkdir(parents=True, exist_ok=True)
    edits = []
    n = stale_ack.fix_stale(now=NOW, db_path=db, transcripts=str(root), edit=lambda m, t: edits.append((m, t)))
    return n, edits, db


def test_stuck_ack_gets_honest_text_once(tmp_path):
    n, edits, db = run_check(tmp_path, [(7, "sent", SPAWN, ts(300))])
    assert n == 1
    assert edits == [(7, "🔄 בודק — 📋 מחכה ל-agent, הוא עוד לא התחיל")]
    # the edit is logged, so the next heartbeat leaves it alone
    edits2 = []
    stale_ack.fix_stale(now=NOW, db_path=db, transcripts=str(tmp_path / "projects"),
                        edit=lambda m, t: edits2.append((m, t)))
    assert edits2 == []


def test_fresh_spawn_inside_grace_untouched(tmp_path):
    assert run_check(tmp_path, [(7, "sent", SPAWN, ts(55))])[1] == []


def test_ack_moved_on_or_deleted_untouched(tmp_path):
    rows = [(7, "sent", SPAWN, ts(600)), (7, "edited", "reading", ts(590)),
            (8, "sent", SPAWN, ts(600)), (8, "deleted", None, ts(500))]
    assert run_check(tmp_path, rows)[1] == []


def test_old_rows_and_other_texts_untouched(tmp_path):
    rows = [(7, "sent", SPAWN, ts(4000)), (8, "sent", "voice: x — מייצר agent", ts(300)),
            (9, "sent", "🔄 בודק — 📋 בתור אצל ה-agent שכבר עובד על זה", ts(300))]
    assert run_check(tmp_path, rows)[1] == []


def test_worker_edit_not_logged_is_respected(tmp_path):
    """ack_log misses worker edits, so the transcript decides: a live status is never overwritten."""
    root = tmp_path / "projects" / "s" / "subagents"
    root.mkdir(parents=True)
    line = {"message": {"content": [{"name": "edit_message", "input": {"message_id": "7", "text": "live"}}]}}
    (root / "agent-x.jsonl").write_text(json.dumps(line, separators=(",", ":")), encoding="utf-8")
    assert run_check(tmp_path, [(7, "sent", SPAWN, ts(300))])[1] == []


def test_edit_in_main_session_is_respected(tmp_path):
    (tmp_path / "projects" / "s").mkdir(parents=True)
    (tmp_path / "projects" / "main.jsonl").write_text('{"message_id": 7}', encoding="utf-8")
    assert run_check(tmp_path, [(7, "sent", SPAWN, ts(300))])[1] == []


def test_no_transcripts_means_no_edit(tmp_path):
    db = str(tmp_path / "k.db")
    make_db(db, [(7, "sent", SPAWN, ts(300))])
    edits = []
    stale_ack.fix_stale(now=NOW, db_path=db, transcripts=str(tmp_path / "missing"),
                        edit=lambda m, t: edits.append((m, t)))
    assert edits == []
