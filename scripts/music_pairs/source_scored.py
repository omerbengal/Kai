"""Generate candidate tracks via Groq, enrich with Tunebat, score, return pairs."""
import json
import os
import re
import sys

import requests

from .lookup_tunebat import lookup as tunebat_lookup
from .score_pairs import top_pairs

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

SYSTEM_PROMPT = """You receive structured filters for a DJ set.
List exactly 30 real, released tracks that fit. Return STRICT JSON:
{"tracks": [{"artist": "<name>", "title": "<title>"}, ...]}.
No markdown, no prose, no commentary."""

SYSTEM_PROMPT_WITH_BPM = """You receive structured filters for a DJ set.
List exactly 30 real, released tracks that fit. For each track include your BEST
ESTIMATE of BPM and Camelot key (format like "8A", "11B"). If you're unsure of
the exact value, give a reasonable estimate for the genre — do NOT omit fields.
Return STRICT JSON:
{"tracks": [{"artist": "<name>", "title": "<title>", "bpm": <int>, "camelot_key": "<XA|XB>"}, ...]}.
No markdown, no prose, no commentary."""


def _strip_fence(s: str) -> str:
    return FENCE_RE.sub("", s).strip()


def _seed_candidates(filters: dict, include_bpm: bool = False) -> list:
    key = os.environ["GROQ_API_KEY"]
    user_msg = json.dumps(filters, ensure_ascii=False)
    system = SYSTEM_PROMPT_WITH_BPM if include_bpm else SYSTEM_PROMPT
    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "temperature": 0.7,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
            },
            timeout=45,
        )
        resp.raise_for_status()
        parsed = json.loads(_strip_fence(resp.json()["choices"][0]["message"]["content"]))
        return parsed.get("tracks", []) or []
    except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
        print(f"[source_scored] seed failure: {e}", file=sys.stderr)
        return []


def fetch_pairs_scored(filters: dict, n_pairs: int = 10) -> list:
    """Return list of (track_a, track_b, score) where tracks are enriched dicts.

    Two-pass enrichment: first try Tunebat for real metadata; if too few seeds
    enrich (likely because Tunebat is Cloudflare-blocking us), refetch seeds
    from Groq with BPM/key included and use those as the source of truth.
    """
    seeds = _seed_candidates(filters)
    enriched = []
    if seeds:
        for s in seeds:
            artist, title = s.get("artist"), s.get("title")
            if not artist or not title:
                continue
            data = tunebat_lookup(artist, title)
            if not data:
                continue
            enriched.append({"artist": artist, "title": title, "bpm": data["bpm"], "camelot_key": data["camelot_key"]})
        print(f"[source_scored] tunebat: {len(enriched)}/{len(seeds)} enriched", file=sys.stderr)

    # Fallback: ask Groq for BPM/key directly. Spec error matrix: when external
    # metadata sources fail, fall back to LLM-estimated values rather than empty.
    if len(enriched) < 3:
        print("[source_scored] tunebat too thin, falling back to Groq-estimated BPM/key", file=sys.stderr)
        bpm_seeds = _seed_candidates(filters, include_bpm=True)
        enriched = []
        for s in bpm_seeds:
            artist, title = s.get("artist"), s.get("title")
            bpm, key = s.get("bpm"), s.get("camelot_key")
            if not (artist and title and isinstance(bpm, int) and isinstance(key, str)):
                continue
            enriched.append({"artist": artist, "title": title, "bpm": bpm, "camelot_key": key})
        print(f"[source_scored] groq-estimated: {len(enriched)}/{len(bpm_seeds)} usable", file=sys.stderr)

    return top_pairs(enriched, n=n_pairs, energy_curve=filters.get("energy_curve"))
