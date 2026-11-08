# -*- coding: utf-8 -*-
"""wa.py reads a private chat under BOTH its phone JID and its LID twin (08/11/2026)."""
import json
import sqlite3
import sys

sys.path.insert(0, "D:/Kai/bots")
sys.path.insert(0, "D:/Kai/scripts")
import wa  # noqa: E402

PN, LID = "<phone>", "271200000001"


def _dbs(tmp_path):
    msgs = tmp_path / "messages.db"
    c = sqlite3.connect(msgs)
    c.execute("CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TEXT)")
    c.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TEXT,"
              " is_from_me INTEGER, media_type TEXT)")
    c.execute("INSERT INTO chats VALUES (?,?,?)", (f"{PN}@s.whatsapp.net", "דנה בדיקה", "2026-07-24 10:43:09+03:00"))
    c.execute("INSERT INTO chats VALUES (?,?,?)", (f"{LID}@lid", LID, "2026-11-08 12:12:14+02:00"))
    rows = [
        ("1", f"{PN}@s.whatsapp.net", "<phone>", "3 שאלות", "2026-11-08 12:07:11.0386795+02:00", 1, None),
        ("2", f"{LID}@lid", LID, "1. כמו בפעם הקודמת", "2026-11-08 12:09:32+02:00", 0, None),
        ("3", f"{LID}@lid", LID, "כן צריך את 3", "2026-11-08 12:09:58+02:00", 0, None),
        ("4", "999@lid", "999", "צ'אט אחר", "2026-11-08 12:10:00+02:00", 0, None),
    ]
    c.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    dev = tmp_path / "whatsapp.db"
    d = sqlite3.connect(dev)
    d.execute("CREATE TABLE whatsmeow_lid_map (lid TEXT PRIMARY KEY, pn TEXT)")
    d.execute("INSERT INTO whatsmeow_lid_map VALUES (?,?)", (LID, PN))
    d.commit()
    d.close()
    cfg = tmp_path / "bots.json"
    cfg.write_text(json.dumps({"wa_aliases": {}}), encoding="utf-8")
    return str(msgs), str(dev), str(cfg)


def test_chat_jids_both_directions(tmp_path):
    _, dev, _ = _dbs(tmp_path)
    assert wa.chat_jids(f"{PN}@s.whatsapp.net", dev) == [f"{PN}@s.whatsapp.net", f"{LID}@lid"]
    assert wa.chat_jids(f"{LID}@lid", dev) == [f"{LID}@lid", f"{PN}@s.whatsapp.net"]
    assert wa.chat_jids("123-456@g.us", dev) == ["123-456@g.us"]
    assert wa.chat_jids("555@lid", dev) == ["555@lid"]  # no mapping


def test_read_by_name_includes_lid_replies(tmp_path):
    msgs, dev, cfg = _dbs(tmp_path)
    out = wa.read("דנה", limit=50, db_path=msgs, config_path=cfg, device_db=dev)
    texts = [m["text"] for m in out["messages"]]
    assert texts == ["3 שאלות", "1. כמו בפעם הקודמת", "כן צריך את 3"]
    assert out["messages"][0]["time"] == "08/11 12:07"  # 7-digit fraction parsed


def test_read_since_hours_window_uses_utc(tmp_path, monkeypatch):
    msgs, dev, cfg = _dbs(tmp_path)
    out = wa.read("דנה", limit=50, since_hours=24 * 365 * 10, db_path=msgs, config_path=cfg, device_db=dev)
    assert out["count"] == 3


def test_search_in_chat_includes_lid(tmp_path):
    msgs, dev, cfg = _dbs(tmp_path)
    out = wa.search("צריך", chat="דנה", db_path=msgs, config_path=cfg, device_db=dev)
    assert [m["text"] for m in out["messages"]] == ["כן צריך את 3"]
