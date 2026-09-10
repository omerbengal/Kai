import json
import pytest
import responses
from scripts.music_pairs.parse_vibe import parse_vibe, strip_prefix


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _groq_response(payload: dict):
    return {
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]
    }


def test_strip_prefix_removes_trigger():
    assert strip_prefix("שילובי שירים: סט אפרו האוס") == "סט אפרו האוס"
    assert strip_prefix("שילובי שירים:סט") == "סט"
    assert strip_prefix("סט אפרו האוס") == "סט אפרו האוס"


@responses.activate
def test_parse_vibe_returns_structured_filters(fake_groq_key):
    expected = {
        "genres": ["afro house", "deep house"],
        "bpm_min": 118,
        "bpm_max": 124,
        "energy_curve": "warmup→peak",
        "mood": ["nocturnal", "summer"],
        "language": "en",
    }
    responses.add(responses.POST, GROQ_URL, json=_groq_response(expected), status=200)
    out = parse_vibe("סט אחרי-חצות אפרו האוס לקראת קיץ")
    assert out == expected


@responses.activate
def test_parse_vibe_strips_markdown_fence(fake_groq_key):
    payload = {"genres": ["techno"], "bpm_min": 130, "bpm_max": 138, "energy_curve": "peak", "mood": [], "language": "en"}
    wrapped = "```json\n" + json.dumps(payload) + "\n```"
    responses.add(responses.POST, GROQ_URL, json={"choices": [{"message": {"content": wrapped}}]}, status=200)
    out = parse_vibe("טכנו קשה")
    assert out["genres"] == ["techno"]


@responses.activate
def test_parse_vibe_retries_once_on_invalid_json(fake_groq_key):
    bad = {"choices": [{"message": {"content": "not json at all"}}]}
    good = _groq_response({"genres": ["house"], "bpm_min": 120, "bpm_max": 124, "energy_curve": None, "mood": [], "language": "en"})
    responses.add(responses.POST, GROQ_URL, json=bad, status=200)
    responses.add(responses.POST, GROQ_URL, json=good, status=200)
    out = parse_vibe("האוס")
    assert out["genres"] == ["house"]


@responses.activate
def test_parse_vibe_raises_on_repeated_failure(fake_groq_key):
    bad = {"choices": [{"message": {"content": "nope"}}]}
    responses.add(responses.POST, GROQ_URL, json=bad, status=200)
    responses.add(responses.POST, GROQ_URL, json=bad, status=200)
    with pytest.raises(ValueError):
        parse_vibe("nonsense")
