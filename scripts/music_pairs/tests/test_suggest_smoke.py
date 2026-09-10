"""End-to-end smoke test with everything mocked."""
import json
import responses
from scripts.music_pairs import suggest


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TUNEBAT_URL = "https://api.tunebat.com/api/tracks/search"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"


def _groq(payload):
    return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}


@responses.activate
def test_run_pipeline_produces_formatted_message(tmp_cache_dir, fake_groq_key, fake_spotify_creds):
    # 1) parse_vibe Groq call
    responses.add(responses.POST, GROQ_URL, json=_groq({
        "genres": ["afro house"], "bpm_min": 118, "bpm_max": 124,
        "energy_curve": "warmup→peak", "mood": ["nocturnal"], "language": "en",
    }), status=200)
    # 2) source_scored seed Groq call
    responses.add(responses.POST, GROQ_URL, json=_groq({
        "tracks": [
            {"artist": "Artist1", "title": "Track1"},
            {"artist": "Artist2", "title": "Track2"},
            {"artist": "Artist3", "title": "Track3"},
        ]
    }), status=200)
    # 3) Tunebat lookups (3x)
    responses.add(responses.POST, TUNEBAT_URL, json={"data": {"items": [{"b": 120, "ck": "8A"}]}}, status=200)
    responses.add(responses.POST, TUNEBAT_URL, json={"data": {"items": [{"b": 122, "ck": "9A"}]}}, status=200)
    responses.add(responses.POST, TUNEBAT_URL, json={"data": {"items": [{"b": 124, "ck": "8A"}]}}, status=200)
    # 4) Spotify token + searches
    responses.add(responses.POST, SPOTIFY_TOKEN_URL, json={"access_token": "tok", "expires_in": 3600}, status=200)
    for _ in range(6):
        responses.add(responses.GET, SPOTIFY_SEARCH_URL,
                      json={"tracks": {"items": [{"external_urls": {"spotify": "https://open.spotify.com/track/x"}}]}},
                      status=200)
    # 5) compose_reasons Groq call
    responses.add(responses.POST, GROQ_URL, json=_groq({
        "1": "מעבר חלק בגרוב", "2": "אותו key בדיוק", "3": "BPM עולה בעדינות",
    }), status=200)

    # No pytracklists in test env -> must explicitly allow algorithmic fallback.
    msg = suggest.run("שילובי שירים: סט אחרי-חצות אפרו האוס לקראת קיץ",
                      allow_algorithmic_fallback=True)
    assert "🎧" in msg
    assert "8A" in msg
    assert "←" in msg
    assert "1." in msg
    # Fallback warning must be loud and visible.
    assert "fallback" in msg.lower() or "אלגוריתמי" in msg
    # ΔBPM label visible.
    assert "ΔBPM" in msg


def test_no_djset_source_raises_by_default(tmp_cache_dir, fake_groq_key):
    """Default mode: no pytracklists = explicit failure, never silent fallback."""
    import pytest

    # parse_vibe must succeed so we reach the source check.
    @responses.activate
    def _go():
        responses.add(responses.POST, GROQ_URL, json=_groq({
            "genres": ["afro house"], "bpm_min": 118, "bpm_max": 124,
            "energy_curve": None, "mood": [], "language": "en",
        }), status=200)
        with pytest.raises(suggest.NoDJSetSourceError):
            suggest.run("שילובי שירים: אפרו האוס", allow_algorithmic_fallback=False)

    _go()
