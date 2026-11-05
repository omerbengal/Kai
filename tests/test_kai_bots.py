"""Multi-bot stage 1: watchdog exclusion, bot history, group/topic discovery."""
import json
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "bots"))

import kai_bots  # noqa: E402
import kai_watchdog  # noqa: E402
import bot_tg  # noqa: E402
import setup_bot  # noqa: E402


def test_watchdog_ignores_bot_sessions():
    assert kai_watchdog.main_claude_running({100, 200}, {200}) is True
    assert kai_watchdog.main_claude_running({200}, {200}) is False   # only the bot is up = main is DOWN
    assert kai_watchdog.main_claude_running(set(), set()) is False


def test_bot_pids_from_pid_files(tmp_path):
    home = tmp_path / "miluim"
    home.mkdir()
    (home / "claude.pid").write_text(str(os.getpid()))  # python, not claude -> not counted
    cfg = tmp_path / "bots.json"
    cfg.write_text(json.dumps({"bots": {"miluim": {"home": str(home)}}}))
    assert kai_bots.bot_claude_pids(str(cfg)) == set()


def test_parse_channel_with_topic():
    prompt = ('<channel source="telegram" chat_id="-1001234" message_id="55" message_thread_id="7" '
              'user="omer" ts="2026-11-05T10:00:00Z">כמה נרשמו?</channel>')
    c = bot_tg.parse_channel(prompt)
    assert c["chat_id"] == "-1001234" and c["thread_id"] == "7" and c["message_id"] == "55"
    assert c["text"] == "כמה נרשמו?"


def test_history_roundtrip(tmp_path):
    db = str(tmp_path / "k.db")
    bot_tg.hook_save("miluim", "incoming", {"prompt": '<channel chat_id="-1" message_id="5" '
                                                       'message_thread_id="7" ts="2026-11-05T10:00:00Z">היי</channel>'}, db)
    bot_tg.hook_save("miluim", "outgoing", {"tool_input": {"chat_id": "-1", "message_thread_id": "7", "text": "שלום"},
                                            "tool_response": {"content": [{"text": "sent (id: 6)"}]}}, db)
    out = bot_tg.context("miluim", 20, db)
    assert "היי" in out and "שלום" in out and "id=6" in out


def test_find_group_and_topic():
    ups = [
        {"my_chat_member": {"chat": {"id": -100555, "type": "supergroup", "title": "הצוות של קאי", "is_forum": True}}},
        {"message": {"chat": {"id": -100555, "type": "supergroup", "is_forum": True}, "message_thread_id": 9,
                     "is_topic_message": True, "forum_topic_created": {"name": "תחום א"}}},
        {"message": {"chat": {"id": -100555, "type": "supergroup", "is_forum": True}, "message_thread_id": 9,
                     "is_topic_message": True, "text": "בדיקה",
                     "reply_to_message": {"forum_topic_created": {"name": "תחום א"}}}},
    ]
    assert setup_bot.find_group_and_topic(ups, "תחום") == ("-100555", "9", "הצוות של קאי")


def test_setup_refuses_main_token():
    with pytest.raises(SystemExit):
        setup_bot.main(["x", "--bot", "miluim", "--token", "<REDACTED_SECRET>:abc"])
