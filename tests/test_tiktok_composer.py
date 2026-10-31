"""Composer fidelity checks (31/10/2026): the post may only carry what the transcript says.

Run: D:/Kai/.venv/Scripts/python.exe -m pytest D:/Kai/tests/test_tiktok_composer.py
"""
import json
import os
import random
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import tiktok_summary as t  # noqa: E402

TRANSCRIPT = "It missed 2 .7 times as much, about 1 % that day, 115 milliseconds, 11 series."
FMT = {"ending_key": "fact", "emojis": ["🎯"]}


def good_post():
    return "\n".join(f"שורה {letter} עם פרט." for letter in "אבגדהוז").replace("שורה א", "פי 2.7 ו-1%")


def test_invented_number_is_caught():
    assert t._unsupported_numbers("טעה ב-30% על 12 סדרות", TRANSCRIPT) == ["12", "30"]


def test_spaced_decimals_and_percents_from_supadata_match():
    assert t._unsupported_numbers("פי 2.7, בערך 1%, 115 מילישניות, 11 סדרות", TRANSCRIPT) == []


def test_list_numbering_and_item_count_are_not_facts():
    post = "3 דברים שכדאי לדעת\n1. 🎯 11 סדרות\n🎲 2. פי 2.7"
    assert t._unsupported_numbers(post, TRANSCRIPT) == []


def test_creator_first_person_is_a_violation():
    assert any("גוף ראשון" in p for p in t._violations("הרצתי את זה על הלפטופ שלי", FMT))
    assert not any("גוף ראשון" in p for p in t._violations("הריצו אותו על לפטופ", FMT))


def test_invented_number_triggers_a_retry_and_the_clean_version_wins(monkeypatch):
    posts = [good_post().replace("שורה ג", "חסך 30%"), good_post()]
    seen = []

    def compose(messages):
        seen.append(messages)
        return posts[len(seen) - 1]

    monkeypatch.setattr(t, "_claude_compose", compose)
    monkeypatch.setattr(t, "record_format", lambda f: None)
    monkeypatch.setattr(t, "clean_post", lambda text, fmt: text)
    fmt = t.pick_format(random.Random(1))
    fmt["ending_key"] = "fact"

    assert t.summarize_hebrew(TRANSCRIPT, fmt=fmt) == posts[1]
    assert "30" in seen[1][-1]["content"]


def test_groq_fallback_when_claude_fails(monkeypatch, capsys):
    def broken(messages):
        raise ValueError("no claude")

    def groq(url, **kwargs):
        reply = {"choices": [{"message": {"content": good_post()}}]}
        return types.SimpleNamespace(status_code=200, text=json.dumps(reply), json=lambda: reply)

    monkeypatch.setattr(t, "_claude_compose", broken)
    monkeypatch.setattr(t.requests, "post", groq)
    monkeypatch.setattr(t, "record_format", lambda f: None)
    monkeypatch.setattr(t, "clean_post", lambda text, fmt: text)
    fmt = t.pick_format(random.Random(1))
    fmt["ending_key"] = "fact"

    assert t.summarize_hebrew(TRANSCRIPT, fmt=fmt) == good_post()
    assert "COMPOSER_FALLBACK" in capsys.readouterr().err


def test_headless_call_is_isolated(monkeypatch):
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return types.SimpleNamespace(returncode=0, stdout="פוסט".encode("utf-8"), stderr=b"")

    monkeypatch.setattr(t.subprocess, "run", run)

    assert t._claude_compose([{"role": "user", "content": "תמלול"}]) == "פוסט"
    cmd, kwargs = calls[0]
    for flag in ("--setting-sources", "--strict-mcp-config", "--mcp-config", "--tools"):
        assert flag in cmd
    assert "תמלול" not in " ".join(cmd)
    assert "תמלול".encode("utf-8") in kwargs["input"]
