import json
import responses
from scripts.music_pairs.spotify_search import search_track, get_token, TOKEN_URL, SEARCH_URL


@responses.activate
def test_get_token_fetches_and_caches(tmp_cache_dir, fake_spotify_creds):
    responses.add(responses.POST, TOKEN_URL, json={"access_token": "tok-1", "expires_in": 3600}, status=200)
    t = get_token()
    assert t == "tok-1"
    t2 = get_token()
    assert t2 == "tok-1"
    assert len(responses.calls) == 1


def test_get_token_returns_none_without_creds(tmp_cache_dir, monkeypatch):
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    assert get_token() is None


@responses.activate
def test_search_track_returns_url(tmp_cache_dir, fake_spotify_creds):
    responses.add(responses.POST, TOKEN_URL, json={"access_token": "tok", "expires_in": 3600}, status=200)
    responses.add(
        responses.GET,
        SEARCH_URL,
        json={"tracks": {"items": [{"external_urls": {"spotify": "https://open.spotify.com/track/abc"}}]}},
        status=200,
    )
    url = search_track("Daft Punk", "One More Time")
    assert url == "https://open.spotify.com/track/abc"


@responses.activate
def test_search_track_no_results_returns_none(tmp_cache_dir, fake_spotify_creds):
    responses.add(responses.POST, TOKEN_URL, json={"access_token": "tok", "expires_in": 3600}, status=200)
    responses.add(responses.GET, SEARCH_URL, json={"tracks": {"items": []}}, status=200)
    assert search_track("nobody", "nothing") is None


def test_search_track_returns_none_without_creds(tmp_cache_dir, monkeypatch):
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    assert search_track("a", "b") is None


@responses.activate
def test_search_track_http_error_returns_none(tmp_cache_dir, fake_spotify_creds):
    responses.add(responses.POST, TOKEN_URL, json={"access_token": "tok", "expires_in": 3600}, status=200)
    responses.add(responses.GET, SEARCH_URL, status=500)
    assert search_track("a", "b") is None
