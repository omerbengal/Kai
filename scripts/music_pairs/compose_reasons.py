"""Compose a one-line Hebrew 'why this pair works' for each pair, via Groq."""
import json
import os
import re

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

SYSTEM_PROMPT = """You are Kai, Omer's DJ-curating assistant.
For each numbered pair, write ONE concise Hebrew sentence (<= 12 words) explaining
why the transition works musically: harmonic relation, BPM proximity, energy,
or vibe match. Return STRICT JSON object: {"1": "...", "2": "...", ...}.
No prose outside the JSON. No markdown fences."""


def _strip_fence(s: str) -> str:
    return FENCE_RE.sub("", s).strip()


def _format_pairs_for_prompt(pairs: list) -> str:
    lines = []
    for i, p in enumerate(pairs, start=1):
        a, b = p["track_a"], p["track_b"]
        lines.append(
            f"{i}. {a['artist']} - {a['title']} ({a.get('bpm')} BPM, {a.get('camelot_key')}) "
            f">> {b['artist']} - {b['title']} ({b.get('bpm')} BPM, {b.get('camelot_key')})"
        )
    return "\n".join(lines)


def compose_reasons(pairs: list, vibe_summary: str) -> dict:
    key = os.environ["GROQ_API_KEY"]
    user_msg = f"וייב: {vibe_summary}\n\nפיירים:\n{_format_pairs_for_prompt(pairs)}"
    fallback = {str(i): "" for i in range(1, len(pairs) + 1)}
    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "temperature": 0.6,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(_strip_fence(content))
        if isinstance(parsed, dict):
            return {**fallback, **{str(k): str(v) for k, v in parsed.items()}}
    except (requests.RequestException, json.JSONDecodeError, KeyError):
        pass
    return fallback
