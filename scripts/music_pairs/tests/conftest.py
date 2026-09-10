"""Shared fixtures for music_pairs tests."""
import os
import pytest


@pytest.fixture
def tmp_cache_dir(tmp_path, monkeypatch):
    """Point CACHE_DIR at a tmp directory for the duration of a test."""
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("MUSIC_PAIRS_CACHE_DIR", str(cache))
    return cache


@pytest.fixture
def fake_groq_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")


@pytest.fixture
def fake_spotify_creds(monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "test-client-secret")
