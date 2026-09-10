import json
import responses
from scripts.music_pairs.compose_reasons import compose_reasons


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _pair(i):
    return {
        "track_a": {"artist": f"A{i}", "title": f"T{i}", "bpm": 120, "camelot_key": "8A"},
        "track_b": {"artist": f"B{i}", "title": f"U{i}", "bpm": 122, "camelot_key": "9A"},
    }


@responses.activate
def test_compose_returns_one_reason_per_pair(fake_groq_key):
    pairs = [_pair(1), _pair(2)]
    body = {"choices": [{"message": {"content": json.dumps({"1": "מעבר חלק בגרוב", "2": "אותו key בדיוק"})}}]}
    responses.add(responses.POST, GROQ_URL, json=body, status=200)
    out = compose_reasons(pairs, vibe_summary="אפרו האוס")
    assert out == {"1": "מעבר חלק בגרוב", "2": "אותו key בדיוק"}


@responses.activate
def test_compose_strips_markdown_fence(fake_groq_key):
    pairs = [_pair(1)]
    wrapped = "```json\n" + json.dumps({"1": "סיבה"}) + "\n```"
    responses.add(responses.POST, GROQ_URL, json={"choices": [{"message": {"content": wrapped}}]}, status=200)
    out = compose_reasons(pairs, vibe_summary="x")
    assert out["1"] == "סיבה"


@responses.activate
def test_compose_falls_back_to_default_on_failure(fake_groq_key):
    pairs = [_pair(1)]
    responses.add(responses.POST, GROQ_URL, json={"choices": [{"message": {"content": "garbage"}}]}, status=200)
    out = compose_reasons(pairs, vibe_summary="x")
    assert "1" in out


@responses.activate
def test_compose_prompt_includes_all_pairs(fake_groq_key):
    pairs = [_pair(1), _pair(2), _pair(3)]
    body = {"choices": [{"message": {"content": json.dumps({"1": "a", "2": "b", "3": "c"})}}]}
    responses.add(responses.POST, GROQ_URL, json=body, status=200)
    compose_reasons(pairs, vibe_summary="x")
    sent = json.loads(responses.calls[0].request.body)
    user_msg = sent["messages"][-1]["content"]
    assert "A1" in user_msg and "A2" in user_msg and "A3" in user_msg
