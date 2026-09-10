"""Build the final Hebrew RTL Telegram message — pure function."""

RLM = "‏"


def _line(text: str) -> str:
    return f"{RLM}{text}"


def _track_label(t: dict) -> str:
    return f"{t['artist']} - {t['title']}"


def format_message(pairs: list, vibe_summary: str) -> str:
    """pairs: list of {track_a, track_b, reason, spotify_a, spotify_b}"""
    lines = [_line(f"🎧 שילובי שירים - {vibe_summary}"), ""]
    any_missing_link = False
    for i, p in enumerate(pairs, start=1):
        a, b = p["track_a"], p["track_b"]
        delta_bpm = abs((a.get("bpm") or 0) - (b.get("bpm") or 0))
        src_a = a.get("source") or "?"
        src_b = b.get("source") or "?"
        src_tag = src_a if src_a == src_b else f"{src_a}/{src_b}"
        lines.append(_line(f"{i}. {_track_label(a)} ← {_track_label(b)}"))
        lines.append(_line(
            f"   ΔBPM={delta_bpm} ({a['bpm']}←{b['bpm']}) · {a['camelot_key']}←{b['camelot_key']} · {p['reason']}"
        ))
        lines.append(_line(f"   📀 source: {src_tag}"))
        link_a, link_b = p.get("spotify_a"), p.get("spotify_b")
        if link_a and link_b:
            lines.append(_line(f"   🔗 {link_a}  |  {link_b}"))
        elif link_a or link_b:
            lines.append(_line(f"   🔗 {link_a or link_b}"))
            any_missing_link = True
        else:
            any_missing_link = True
        lines.append("")
    if any_missing_link:
        lines.append(_line("(הקישורים החסרים = Spotify search לא הצליח)"))
    return "\n".join(lines)
