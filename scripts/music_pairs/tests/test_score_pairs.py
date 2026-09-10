from scripts.music_pairs.score_pairs import (
    MAX_BPM_DIFF,
    DJSET_SOURCE_BONUS,
    score_pair,
    top_pairs,
)


T1 = {"artist": "A1", "title": "T1", "bpm": 122, "camelot_key": "8A"}
T2 = {"artist": "A2", "title": "T2", "bpm": 122, "camelot_key": "8A"}  # same key, same bpm
T3 = {"artist": "A3", "title": "T3", "bpm": 124, "camelot_key": "9A"}  # adjacent key, +2 bpm
T4 = {"artist": "A4", "title": "T4", "bpm": 142, "camelot_key": "2B"}  # far on both


def test_same_key_same_bpm_max_score():
    score = score_pair(T1, T2, energy_curve=None)
    # bpm = 100, harmonic = 30 -> 130
    assert score >= 120


def test_adjacent_key_close_bpm_high_score():
    # +2 bpm under new steeper curve: 100 - 2*30 = 40, +20 harmonic = 60
    score = score_pair(T1, T3, energy_curve=None)
    assert score >= 50 and score < 130


def test_far_pair_rejected_by_bpm_cap():
    # |122 - 142| = 20 > MAX_BPM_DIFF, hard-rejected to 0
    score = score_pair(T1, T4, energy_curve=None)
    assert score == 0


def test_bpm_cap_constant():
    assert MAX_BPM_DIFF == 3


def test_pair_just_inside_cap_scores():
    inside = {"artist": "x", "title": "x", "bpm": 122 + MAX_BPM_DIFF, "camelot_key": "8A"}
    assert score_pair(T1, inside, energy_curve=None) > 0


def test_pair_just_outside_cap_zero():
    outside = {"artist": "x", "title": "x", "bpm": 122 + MAX_BPM_DIFF + 1, "camelot_key": "8A"}
    assert score_pair(T1, outside, energy_curve=None) == 0


def test_djset_provenance_bonus():
    a = {**T1, "source": "1001tracklists"}
    b = {**T2, "source": "1001tracklists"}
    plain = score_pair(T1, T2, energy_curve=None)
    boosted = score_pair(a, b, energy_curve=None)
    assert boosted == plain + DJSET_SOURCE_BONUS


def test_energy_warmup_to_peak_bonus():
    no_bonus = score_pair(T1, T3, energy_curve=None)
    with_bonus = score_pair(T1, T3, energy_curve="warmup→peak")
    assert with_bonus == no_bonus + 10


def test_energy_no_bonus_when_b_slower():
    slower = {"artist": "x", "title": "x", "bpm": 121, "camelot_key": "9A"}
    no_bonus = score_pair(T1, slower, energy_curve=None)
    with_curve = score_pair(T1, slower, energy_curve="warmup→peak")
    assert with_curve == no_bonus


def test_top_pairs_returns_n_no_djset_required():
    tracks = [T1, T2, T3, T4]
    pairs = top_pairs(tracks, n=3, energy_curve=None, require_djset=False)
    # T4 is BPM-rejected against all; only T1/T2/T3 combine -> 3 pairs
    assert 1 <= len(pairs) <= 3
    scores = [p[2] for p in pairs]
    assert scores == sorted(scores, reverse=True)


def test_top_pairs_requires_djset_by_default():
    # No source tags -> default require_djset=True rejects everything
    pairs = top_pairs([T1, T2, T3], n=10, energy_curve=None)
    assert pairs == []


def test_top_pairs_keeps_djset_pairs():
    a = {**T1, "source": "1001tracklists"}
    b = {**T2, "source": "mixesdb"}
    pairs = top_pairs([a, b], n=10, energy_curve=None)
    assert len(pairs) == 1


def test_top_pairs_no_self_pair():
    a = {**T1, "source": "1001tracklists"}
    b = {**T2, "source": "1001tracklists"}
    pairs = top_pairs([a, b], n=10, energy_curve=None)
    for x, y, _ in pairs:
        assert (x["artist"], x["title"]) != (y["artist"], y["title"])


def test_top_pairs_handles_missing_data():
    bad = {"artist": "x", "title": "x", "bpm": None, "camelot_key": None}
    pairs = top_pairs([T1, T2, bad], n=10, energy_curve=None, require_djset=False)
    for a, b, _ in pairs:
        assert a["bpm"] is not None and b["bpm"] is not None
