"""Whisper language choice and the garbage-transcript guard (31/10/2026, Timestral video).

language=he was hard-coded, so an English video became Hebrew-looking nonsense with exit 0.

Run: D:/Kai/.venv/Scripts/python.exe -m pytest D:/Kai/tests/test_tiktok_language.py
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import tiktok_summary as t  # noqa: E402


def result(text, language, logprob):
    return {"text": text, "language": language,
            "segments": [{"start": 0, "end": 10, "avg_logprob": logprob}]}


def fake_whisper(monkeypatch, auto, hebrew=None):
    calls = []

    def call(path, language=None, verbose=True):
        calls.append(language if verbose else f"{language}-plain")
        return hebrew if language == "he" else auto

    monkeypatch.setattr(t, "_whisper_call", call)
    return calls


def test_english_video_is_not_forced_to_hebrew(monkeypatch):
    calls = fake_whisper(monkeypatch, result("A viral post says", "English", -0.19))
    assert t._whisper_transcribe("v.mp4") == "A viral post says"
    assert calls == [None]


def test_hebrew_video_gets_the_exact_old_request(monkeypatch):
    calls = fake_whisper(monkeypatch, result("עובדים בשיץ", "Hebrew", -0.05),
                         {"text": "עובדים בשיטס"})
    assert t._whisper_transcribe("v.mp4") == "עובדים בשיטס"
    assert calls == [None, "he-plain"]


def test_misdetected_hebrew_falls_back_to_forced_hebrew(monkeypatch):
    calls = fake_whisper(monkeypatch, result("ani ohev", "Spanish", -0.9),
                         result("אני אוהב", "Hebrew", -0.1))
    assert t._whisper_transcribe("v.mp4") == "אני אוהב"
    assert calls == [None, "he"]


def test_yiddish_detection_is_retried_as_hebrew(monkeypatch):
    calls = fake_whisper(monkeypatch, result("איך האב", "Yiddish", -0.2),
                         {"text": "אני אוהב"})
    assert t._whisper_transcribe("v.mp4") == "אני אוהב"
    assert calls == [None, "he-plain"]


def test_garbled_hebrew_audio_is_refused(monkeypatch):
    fake_whisper(monkeypatch, result("לה לה", "Hebrew", -1.1), result("לה לה", "Hebrew", -1.0))
    with pytest.raises(t.TranscriptQualityError):
        t._whisper_transcribe("v.mp4")


def test_low_confidence_retry_keeps_the_better_try(monkeypatch):
    fake_whisper(monkeypatch, result("real words", "English", -0.5),
                 result("מילים משובשות", "Hebrew", -0.3))
    assert t._whisper_transcribe("v.mp4") == "מילים משובשות"

    fake_whisper(monkeypatch, result("real words", "English", -0.3))
    assert t._whisper_transcribe("v.mp4") == "real words"


def test_both_tries_untrusted_raises(monkeypatch):
    fake_whisper(monkeypatch, result("la la", "English", -1.2), result("לה לה", "Hebrew", -0.9))
    with pytest.raises(t.TranscriptQualityError):
        t._whisper_transcribe("v.mp4")


def test_no_segments_is_not_treated_as_garbage(monkeypatch):
    fake_whisper(monkeypatch, {"text": "hello", "language": "English"})
    assert t._whisper_transcribe("v.mp4") == "hello"


def test_garbage_stops_the_cascade_and_is_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(t, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(t, "resolve_canonical_url", lambda u: "https://www.tiktok.com/@a/video/123")

    def no_supadata(url):
        raise ValueError("timeout")

    def garbage(url):
        raise t.TranscriptQualityError("bad")

    def playwright_must_not_run(url):
        raise AssertionError("Playwright fetches the same audio")

    monkeypatch.setattr(t, "_supadata_transcript", no_supadata)
    monkeypatch.setattr(t, "_ytdlp_whisper_fallback", garbage)
    monkeypatch.setattr(t, "_playwright_whisper_fallback", playwright_must_not_run)

    with pytest.raises(t.TranscriptQualityError):
        t.get_transcript("https://vt.tiktok.com/x")
    assert not list(tmp_path.glob("*_transcript.json"))


def test_good_transcript_is_still_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(t, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(t, "resolve_canonical_url", lambda u: "https://www.tiktok.com/@a/video/123")

    def no_supadata(url):
        raise ValueError("timeout")

    monkeypatch.setattr(t, "_supadata_transcript", no_supadata)
    monkeypatch.setattr(t, "_ytdlp_whisper_fallback", lambda url: "good text")

    assert t.get_transcript("https://vt.tiktok.com/x")[0] == "good text"
    cached = json.loads((tmp_path / "tiktok_123_transcript.json").read_text(encoding="utf-8"))
    assert cached == {"text": "good text", "source": "ytdlp_whisper"}
