"""Tunebat BPM+Camelot lookup with a permanent file cache."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

TUNEBAT_URL = "https://api.tunebat.com/api/tracks/search"
DEFAULT_CACHE = Path(__file__).parent / "cache"


def _cache_dir() -> Path:
    return Path(os.environ.get("MUSIC_PAIRS_CACHE_DIR", str(DEFAULT_CACHE)))


def _slug(artist: str, title: str) -> str:
    def s(part: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", part.lower())
    return f"{s(artist)}__{s(title)}"


def _cache_path(artist: str, title: str) -> Path:
    d = _cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_slug(artist, title)}.json"


def _read_cache(path: Path):
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return {"bpm": d["bpm"], "camelot_key": d["camelot_key"]}
    except (json.JSONDecodeError, KeyError):
        return None


def _write_cache(path: Path, bpm: int, camelot_key: str):
    path.write_text(
        json.dumps(
            {"bpm": bpm, "camelot_key": camelot_key, "fetched_at_utc": datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def lookup(artist: str, title: str):
    """Return {'bpm': int, 'camelot_key': str} or None."""
    path = _cache_path(artist, title)
    cached = _read_cache(path)
    if cached is not None:
        return cached
    try:
        resp = requests.post(TUNEBAT_URL, json={"term": f"{artist} {title}"}, timeout=15)
        if resp.status_code != 200:
            return None
        items = (resp.json().get("data") or {}).get("items") or []
        if not items:
            return None
        first = items[0]
        bpm = first.get("b")
        key = first.get("ck")
        if not bpm or not key:
            return None
        _write_cache(path, int(bpm), str(key))
        return {"bpm": int(bpm), "camelot_key": str(key)}
    except requests.RequestException:
        return None
