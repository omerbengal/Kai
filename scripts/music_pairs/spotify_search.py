"""Spotify Web API track search with cached client-credentials token."""
import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

TOKEN_URL = "https://accounts.spotify.com/api/token"
SEARCH_URL = "https://api.spotify.com/v1/search"

DEFAULT_CACHE = Path(__file__).parent / "cache"


def _cache_dir() -> Path:
    return Path(os.environ.get("MUSIC_PAIRS_CACHE_DIR", str(DEFAULT_CACHE)))


def _token_path() -> Path:
    d = _cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "_spotify_token.json"


def _read_cached_token():
    p = _token_path()
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        exp = datetime.fromisoformat(d["expires_at_utc"])
        if exp > datetime.now(timezone.utc) + timedelta(seconds=30):
            return d["access_token"]
    except (json.JSONDecodeError, KeyError, ValueError):
        return None
    return None


def _write_cached_token(token: str, expires_in: int):
    exp = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    _token_path().write_text(
        json.dumps({"access_token": token, "expires_at_utc": exp.isoformat()}),
        encoding="utf-8",
    )


def get_token():
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    sec = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not cid or not sec:
        return None
    cached = _read_cached_token()
    if cached:
        return cached
    auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    try:
        resp = requests.post(
            TOKEN_URL,
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials"},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        body = resp.json()
        token = body["access_token"]
        _write_cached_token(token, int(body.get("expires_in", 3600)))
        return token
    except requests.RequestException:
        return None


def search_track(artist: str, title: str):
    """Return Spotify track URL or None."""
    token = get_token()
    if not token:
        return None
    try:
        resp = requests.get(
            SEARCH_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"q": f"track:{title} artist:{artist}", "type": "track", "limit": 1},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        items = resp.json().get("tracks", {}).get("items", [])
        if not items:
            return None
        return items[0]["external_urls"]["spotify"]
    except (requests.RequestException, KeyError):
        return None
