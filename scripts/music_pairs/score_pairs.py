"""Pair scoring - pure functions.

AutoMix constraint: Spotify AutoMix locks to a tiny 2-bar transition when BPM
gap is wide, and the user cannot extend it to 4/8/16 bars. So we enforce a
HARD max BPM diff and reject any candidate that exceeds it.
"""
from itertools import combinations
from typing import Optional

from .camelot import camelot_distance, is_valid_camelot

# Hard cap on |bpm_a - bpm_b|. Anything wider breaks Spotify AutoMix bar-length
# selection. Do not raise this without re-testing AutoMix behavior.
MAX_BPM_DIFF = 3

# Heavy bonus for pairs whose provenance is a real DJ set (1001tracklists,
# mixesdb, tracklist.club, soundcloud DJ uploads, ...). Algorithmic-only pairs
# (no DJ-set source) get 0 here and are typically rejected upstream.
DJSET_SOURCE_BONUS = 500

# Set of source tags we treat as "real DJ set provenance".
DJSET_SOURCES = {
    "1001tracklists",
    "mixesdb",
    "tracklist.club",
    "soundcloud_dj",
}


def _bpm_score(a: int, b: int) -> int:
    # Steeper curve: identical BPM = 100, +1 = 70, +2 = 40, +3 = 10. Anything
    # over MAX_BPM_DIFF is filtered out before scoring anyway.
    return max(0, 100 - abs(a - b) * 30)


def _harmonic_score(key_a: str, key_b: str) -> int:
    if not (is_valid_camelot(key_a) and is_valid_camelot(key_b)):
        return 0
    d = camelot_distance(key_a, key_b)
    if d == 0:
        return 30
    if d == 1:
        return 20
    if d == 2:
        return 5
    return 0


def _is_djset(t: dict) -> bool:
    return t.get("source") in DJSET_SOURCES


def score_pair(track_a: dict, track_b: dict, energy_curve: Optional[str]) -> int:
    bpm_a, bpm_b = track_a.get("bpm"), track_b.get("bpm")
    key_a, key_b = track_a.get("camelot_key"), track_b.get("camelot_key")
    if bpm_a is None or bpm_b is None:
        return 0
    if abs(bpm_a - bpm_b) > MAX_BPM_DIFF:
        return 0  # hard filter — AutoMix-incompatible
    score = _bpm_score(bpm_a, bpm_b) + _harmonic_score(key_a, key_b)
    if energy_curve == "warmup→peak" and bpm_b > bpm_a:
        score += 10
    # Heavy upweight when both tracks come from a real DJ-set source.
    if _is_djset(track_a) and _is_djset(track_b):
        score += DJSET_SOURCE_BONUS
    return score


def _eligible(t: dict) -> bool:
    return t.get("bpm") is not None and t.get("camelot_key") is not None


def top_pairs(tracks: list, n: int, energy_curve: Optional[str], require_djset: bool = True):
    """Return top-N pairs as list of (track_a, track_b, score), score desc.

    Filters applied:
      - both tracks must have bpm + camelot_key
      - |bpm diff| <= MAX_BPM_DIFF
      - if require_djset: both tracks must have a DJ-set source tag
      - score must be > 0
    """
    eligible = [t for t in tracks if _eligible(t)]
    scored = []
    for a, b in combinations(eligible, 2):
        if require_djset and not (_is_djset(a) and _is_djset(b)):
            continue
        s = score_pair(a, b, energy_curve)
        if s <= 0:
            continue
        scored.append((a, b, s))
    scored.sort(key=lambda x: x[2], reverse=True)
    return scored[:n]
