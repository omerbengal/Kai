"""Parse Hebrew vibe string → structured filters via Groq."""
import json
import os
import re

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
PREFIX_RE = re.compile(r"^\s*שילובי שירים:\s*", re.UNICODE)
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

SYSTEM_PROMPT = """You receive a free-form Hebrew vibe description for a DJ set.
Return STRICT JSON only (no prose, no markdown) with this shape:
{
  "genres": ["<english genre tag>", ...],
  "bpm_min": <int>,
  "bpm_max": <int>,
  "energy_curve": "warmup→peak" | "peak" | "cooldown" | null,
  "mood": ["<english mood keyword>", ...],
  "language": "en"
}
Pick BPM ranges sensibly for the genre (afro house ~118-124, techno ~128-140, deep house ~118-122, jazz ~80-110).
Never include explanations."""


def strip_prefix(text: str) -> str:
    return PREFIX_RE.sub("", text).strip()


def _strip_fence(s: str) -> str:
    return FENCE_RE.sub("", s).strip()


def _call_groq(vibe: str) -> str:
    key = os.environ["GROQ_API_KEY"]
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": vibe},
            ],
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def parse_vibe(text: str) -> dict:
    vibe = strip_prefix(text)
    last_err = None
    for _ in range(2):
        raw = _call_groq(vibe)
        cleaned = _strip_fence(raw)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            last_err = e
            continue
    raise ValueError(f"could not parse Groq response as JSON: {last_err}")
