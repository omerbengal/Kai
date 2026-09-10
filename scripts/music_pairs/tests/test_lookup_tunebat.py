import json
import responses
from scripts.music_pairs.lookup_tunebat import lookup, _slug, _cache_path


def test_slug_is_filesystem_safe():
    s = _slug("Daft Punk", "One More Time!")
    assert s == "daft_punk__one_more_time_"
    assert "/" not in s and " " not in s


def test_cache_path_uses_env_dir(tmp_cache_dir):
    p = _cache_path("Daft Punk", "One More Time")
    assert str(p).startswith(str(tmp_cache_dir))
    assert p.name.endswith(".json")


@responses.activate
def test_lookup_hits_cache_on_second_call(tmp_cache_dir):
    p = _cache_path("X", "Y")
    p.write_text(json.dumps({"bpm": 120, "camelot_key": "8A", "fetched_at_utc": "2026-01-01T00:00:00Z"}))
    out = lookup("X", "Y")
    assert out == {"bpm": 120, "camelot_key": "8A"}


@responses.activate
def test_lookup_miss_returns_none_and_does_not_cache(tmp_cache_dir):
    responses.add(responses.POST, "https://api.tunebat.com/api/tracks/search", json={"data": {"items": []}}, status=200)
    out = lookup("Unknown", "Song")
    assert out is None
    assert not _cache_path("Unknown", "Song").exists()


@responses.activate
def test_lookup_success_writes_cache(tmp_cache_dir):
    payload = {"data": {"items": [{"b": 124, "ck": "9A"}]}}
    responses.add(responses.POST, "https://api.tunebat.com/api/tracks/search", json=payload, status=200)
    out = lookup("A", "B")
    assert out == {"bpm": 124, "camelot_key": "9A"}
    cached = json.loads(_cache_path("A", "B").read_text())
    assert cached["bpm"] == 124 and cached["camelot_key"] == "9A"


@responses.activate
def test_lookup_handles_http_error_returns_none(tmp_cache_dir):
    responses.add(responses.POST, "https://api.tunebat.com/api/tracks/search", status=503)
    assert lookup("X", "Z") is None
