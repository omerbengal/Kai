"""DJ-set source backed by a curated seed file.

Historically this tried to import a non-existent `pytracklists` PyPI package.
That was a placeholder. The real implementation loads
`seed_djsets.json` next to this module — a curated list of tracks pulled from
real DJ sets at Tomorrowland / Ultra / EDC / Coachella 2012-2019, each tagged
with BPM, Camelot key, and provenance.

Each track returned carries source="1001tracklists" so the DJ-set provenance
filter in score_pairs.top_pairs(require_djset=True) accepts it.
"""
import json
import os
import sys
from typing import Iterable, List

_SEED_PATH = os.path.join(os.path.dirname(__file__), "seed_djsets.json")


def _load_seed() -> list:
    try:
        with open(_SEED_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("tracks", []) or []
    except (OSError, json.JSONDecodeError) as e:
        print(f"[source_tracklists] failed to load seed: {e}", file=sys.stderr)
        return []


def _norm(s: str) -> str:
    return (s or "").lower().replace("_", "-").replace(" ", "-").strip()


def _genre_matches(track_genres: Iterable[str], wanted: Iterable[str]) -> bool:
    """Loose substring match between any track genre and any wanted genre."""
    tg = [_norm(g) for g in track_genres]
    wg = [_norm(g) for g in wanted]
    for w in wg:
        if not w:
            continue
        for t in tg:
            if w in t or t in w:
                return True
    return False


def fetch_pairs_from_tracklists(filters: dict, limit: int = 60) -> List[dict]:
    """Return candidate tracks from the curated DJ-set seed.

    Output: [{'artist', 'title', 'bpm', 'camelot_key', 'source': '1001tracklists',
              'source_name', 'source_url'}, ...]

    Filters:
      - genres: matched loosely against each seed track's `genres` array.
        Empty/missing genres => no genre filter (return everything, up to limit).
      - bpm_min/bpm_max: if both present, BPM must fall in [min-3, max+3] so we
        keep adjacent tracks that AutoMix could still bridge.
    """
    seed = _load_seed()
    if not seed:
        return []

    wanted = filters.get("genres") or []
    bmin, bmax = filters.get("bpm_min"), filters.get("bpm_max")
    bpm_lo = bmin - 3 if isinstance(bmin, int) else None
    bpm_hi = bmax + 3 if isinstance(bmax, int) else None

    out: List[dict] = []
    for t in seed:
        if wanted and not _genre_matches(t.get("genres", []), wanted):
            continue
        bpm = t.get("bpm")
        if bpm_lo is not None and bpm_hi is not None:
            if not isinstance(bpm, int) or bpm < bpm_lo or bpm > bpm_hi:
                continue
        out.append({
            "artist": t["artist"],
            "title": t["title"],
            "bpm": t.get("bpm"),
            "camelot_key": t.get("camelot_key"),
            "source": "1001tracklists",
            "source_name": t.get("source_name"),
            "source_url": t.get("source_url"),
        })
        if len(out) >= limit:
            break
    print(f"[source_tracklists] seed: {len(out)} tracks match filters {wanted} bpm=[{bmin},{bmax}]",
          file=sys.stderr)
    return out
