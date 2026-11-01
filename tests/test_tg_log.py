"""Every Bot API send by a script lands in kai.db `messages` (Omer msg 21433: his reply to alert
21421 had no context because the alert was never saved). The scan at the bottom fails on any
future script that sends without saving."""
import io
import json
import os
import re
import sqlite3
import sys
import urllib.request

import pytest

sys.path.insert(0, "D:/Kai/scripts")
import tg_log  # noqa: E402

ROOT = "D:/Kai"
SEND_RE = re.compile(r"""[/"'](send(?:Message|Document|Contact|Photo|Video|Audio|Voice|MediaGroup|Location|Poll))\b""")
SAVE_RE = re.compile(r"tg_log\.save_|INSERT INTO messages|_save_outgoing|tg_silent_send\.save")
OK = json.dumps({"ok": True, "result": {"message_id": 777}}).encode()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, telegram_message_id INTEGER, direction TEXT, "
                 "text TEXT, reply_to_message_id INTEGER, reply_to_message_text TEXT, ts TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(tg_log, "DB_PATH", path)
    monkeypatch.setattr(tg_log, "FAILURE_LOG", str(tmp_path / "failures.log"))
    return path


def rows(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    out = [dict(r) for r in conn.execute("SELECT * FROM messages")]
    conn.close()
    return out


def test_row_matches_tg_silent_send_format(db):
    assert tg_log.save_outgoing(777, "התראה", reply_to=12) is True
    r = rows(db)[0]
    assert (r["telegram_message_id"], r["direction"], r["text"], r["reply_to_message_id"]) == (777, "outgoing", "התראה", 12)
    assert r["ts"].endswith("+00:00") and "T" in r["ts"]  # same as datetime.now(timezone.utc).isoformat()


@pytest.mark.parametrize("resp", [OK, OK.decode(), json.loads(OK), io.BytesIO(OK)])
def test_save_response_accepts_every_shape(db, resp):
    assert tg_log.save_response(resp, "x") is True
    assert rows(db)[0]["telegram_message_id"] == 777


@pytest.mark.parametrize("resp", [b'{"ok": false, "description": "bad"}', {"ok": True}, b"not json", None])
def test_rejected_or_broken_response_saves_nothing_and_never_raises(db, resp):
    assert tg_log.save_response(resp, "x") is False
    assert rows(db) == []


def test_db_failure_never_raises_and_keeps_the_text(db, tmp_path, monkeypatch):
    monkeypatch.setattr(tg_log, "DB_PATH", str(tmp_path / "missing_dir" / "t.db"))
    assert tg_log.save_outgoing(777, "טקסט") is False
    entry = json.loads(open(tg_log.FAILURE_LOG, encoding="utf-8").read())
    assert (entry["message_id"], entry["text"]) == (777, "טקסט")


def fake_urlopen(*a, **k):
    return io.BytesIO(OK)


def test_script_alert_is_saved(db, monkeypatch):
    import on_compact
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    on_compact.send_telegram("התראה מסקריפט")
    assert [r["text"] for r in rows(db)] == ["התראה מסקריפט"]


def test_script_alert_survives_db_failure(db, tmp_path, monkeypatch, capsys):
    import on_compact
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(tg_log, "DB_PATH", str(tmp_path / "missing_dir" / "t.db"))
    on_compact.send_telegram("עדיין נשלח")
    assert "send failed" not in capsys.readouterr().err


def test_worker_watchdog_keeps_reply_to(db, monkeypatch):
    import worker_watchdog as ww
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    ww.send_fallback(55)
    assert rows(db)[0]["reply_to_message_id"] == 55


def test_every_sending_script_saves():
    files = [os.path.join(d, f) for top in ("scripts", "voice") for d, _, fs in os.walk(os.path.join(ROOT, top))
             for f in fs if f.endswith(".py") and "__pycache__" not in d]
    assert len(files) > 50
    unsaved = []
    for path in files:
        if os.path.basename(path) == "tg_log.py":
            continue
        src = open(path, encoding="utf-8", errors="replace").read()
        code = "\n".join(ln for ln in src.split("\n") if "api.telegram.org" in ln or "_tg_api(" in ln or "BASE_URL" in ln)
        if SEND_RE.search(code) and not SAVE_RE.search(src):
            unsaved.append(path)
    assert unsaved == []
