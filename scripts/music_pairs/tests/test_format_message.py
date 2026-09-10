from scripts.music_pairs.format_message import format_message

RLM = "‏"


def _pair(idx, link_a="https://open.spotify.com/track/a", link_b="https://open.spotify.com/track/b"):
    return {
        "track_a": {"artist": f"Artist{idx}A", "title": f"Title{idx}A", "bpm": 120 + idx, "camelot_key": "8A"},
        "track_b": {"artist": f"Artist{idx}B", "title": f"Title{idx}B", "bpm": 122 + idx, "camelot_key": "9A"},
        "reason": f"שילוב מספר {idx}",
        "spotify_a": link_a,
        "spotify_b": link_b,
    }


def test_header_has_rlm_and_vibe_summary():
    msg = format_message([_pair(1)], vibe_summary="אפרו האוס קיצי")
    first_line = msg.split("\n", 1)[0]
    assert first_line.startswith(RLM)
    assert "אפרו האוס קיצי" in first_line
    assert "🎧" in first_line


def test_every_line_starts_with_rlm():
    msg = format_message([_pair(1), _pair(2)], vibe_summary="x")
    for line in msg.split("\n"):
        if line == "":
            continue
        assert line.startswith(RLM), f"missing RLM on: {line!r}"


def test_uses_left_arrow_not_right():
    msg = format_message([_pair(1)], vibe_summary="x")
    assert "←" in msg
    assert "→" not in msg


def test_includes_bpm_and_keys():
    msg = format_message([_pair(1)], vibe_summary="x")
    assert "121" in msg and "123" in msg
    assert "8A" in msg and "9A" in msg


def test_includes_delta_bpm_prominently():
    msg = format_message([_pair(1)], vibe_summary="x")
    # ΔBPM=2 (121 vs 123) must appear explicitly so Omer can validate tightness.
    assert "ΔBPM=2" in msg


def test_includes_source_tag():
    p = _pair(1)
    p["track_a"]["source"] = "1001tracklists"
    p["track_b"]["source"] = "1001tracklists"
    msg = format_message([p], vibe_summary="x")
    assert "1001tracklists" in msg
    assert "source" in msg


def test_includes_reason():
    msg = format_message([_pair(1)], vibe_summary="x")
    assert "שילוב מספר 1" in msg


def test_missing_link_omits_link_and_adds_footer():
    p = _pair(1, link_a=None, link_b=None)
    msg = format_message([p], vibe_summary="x")
    assert "open.spotify.com" not in msg
    assert "Spotify search לא הצליח" in msg


def test_all_links_present_no_footer():
    msg = format_message([_pair(1)], vibe_summary="x")
    assert "Spotify search לא הצליח" not in msg


def test_numbering_starts_at_1():
    msg = format_message([_pair(1), _pair(2)], vibe_summary="x")
    assert "1." in msg and "2." in msg
