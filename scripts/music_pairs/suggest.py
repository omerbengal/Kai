"""Orchestrator: vibe -> pairs -> formatted Telegram message."""
import argparse
import io
import sys

# Force UTF-8 on Windows so Hebrew/emoji don't crash on print
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from . import parse_vibe as pv
from . import source_tracklists as st
from . import source_scored as ss
from . import spotify_search as sp
from . import compose_reasons as cr
from . import format_message as fm
from .score_pairs import MAX_BPM_DIFF, top_pairs


class NoDJSetSourceError(RuntimeError):
    """Raised when no real DJ-set provenance is available.

    AutoMix requirement: pairs MUST come from real DJ sets (1001tracklists,
    mixesdb, tracklist.club, soundcloud DJ uploads). Pure algorithmic BPM
    matching is not acceptable - it broke AutoMix in Automix Try #1.
    """


def _vibe_summary(filters: dict) -> str:
    genres = ", ".join(filters.get("genres") or [])
    bmin, bmax = filters.get("bpm_min"), filters.get("bpm_max")
    bpm = f"{bmin}-{bmax} BPM" if bmin and bmax else ""
    parts = [p for p in [genres, bpm] if p]
    return " | ".join(parts) or "מותאם"


def _pair_dicts_from_scored(scored_triples: list) -> list:
    out = []
    for a, b, _ in scored_triples:
        out.append({"track_a": a, "track_b": b})
    return out


def _attach_spotify(pairs: list):
    for p in pairs:
        a, b = p["track_a"], p["track_b"]
        p["spotify_a"] = sp.search_track(a["artist"], a["title"])
        p["spotify_b"] = sp.search_track(b["artist"], b["title"])


def run(vibe_text: str, n_pairs: int = 10, no_tracklists: bool = False,
        allow_algorithmic_fallback: bool = False) -> str:
    """Build pairs.

    Order of operations:
      1. Pull candidate tracks from real DJ-set sources via source_tracklists
         (1001tracklists / mixesdb / tracklist.club / soundcloud DJ uploads).
      2. Score them with HARD filter |ΔBPM| <= MAX_BPM_DIFF (3) and a heavy
         bonus when both tracks share DJ-set provenance.
      3. If no DJ-set candidates exist AND allow_algorithmic_fallback is True,
         fall back to source_scored with a loud warning prefix in the output.
         By default (False) this raises NoDJSetSourceError - fail loud, never
         silently regress to algorithmic mode.
    """
    print("[parse_vibe] start", file=sys.stderr)
    filters = pv.parse_vibe(vibe_text)
    print(f"[parse_vibe] ok: {filters}", file=sys.stderr)

    tl_candidates = [] if no_tracklists else st.fetch_pairs_from_tracklists(filters)
    fallback_warning = None
    pairs = []
    if tl_candidates:
        # Score using the real DJ-set source pool; enforce tight BPM + provenance.
        scored = top_pairs(
            tl_candidates,
            n=n_pairs,
            energy_curve=filters.get("energy_curve"),
            require_djset=True,
        )
        pairs = _pair_dicts_from_scored(scored)
        print(f"[source_tracklists] using {len(pairs)} pairs after AutoMix filter "
              f"(|ΔBPM|<={MAX_BPM_DIFF}, DJ-set provenance)", file=sys.stderr)
        if not pairs:
            # Tracklists returned data but no pair survived the ±3 BPM filter.
            return (f"‏🤔 מצאתי טראקים מסטים אמיתיים אבל אף זוג לא עמד ב-|ΔBPM|≤{MAX_BPM_DIFF} "
                    f"(תנאי AutoMix). נסה וייב יותר צר ב-BPM.")
    else:
        if not allow_algorithmic_fallback:
            raise NoDJSetSourceError(
                "No DJ-set provenance available (pytracklists missing or returned 0 results). "
                "Algorithmic fallback is disabled - AutoMix requires real DJ-set sources."
            )
        # Explicit, loud fallback to algorithmic mode (off by default).
        scored = ss.fetch_pairs_scored(filters, n_pairs=n_pairs)
        # Even in fallback, enforce ΔBPM cap.
        scored = [(a, b, s) for (a, b, s) in scored if abs(a["bpm"] - b["bpm"]) <= MAX_BPM_DIFF]
        print(f"[source_scored] FALLBACK got {len(scored)} pairs (no DJ-set source!)", file=sys.stderr)
        pairs = _pair_dicts_from_scored(scored)
        fallback_warning = (
            f"⚠️ אין מקור DJ-set זמין - חזרתי ל-fallback אלגוריתמי. "
            f"AutoMix עלול לא לעבוד טוב. הוסף pytracklists לסביבה."
        )

    if len(pairs) < 3:
        return "‏🤔 לא מצאתי שילובים טובים - נסה וייב אחר (יותר ספציפי לז'אנר/BPM)"

    _attach_spotify(pairs)
    reasons = cr.compose_reasons(pairs, vibe_summary=_vibe_summary(filters))
    for i, p in enumerate(pairs, start=1):
        p["reason"] = reasons.get(str(i), "")
    msg = fm.format_message(pairs, vibe_summary=_vibe_summary(filters))
    if fallback_warning:
        msg = f"‏{fallback_warning}\n\n" + msg
    return msg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vibe", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--no-tracklists", action="store_true")
    parser.add_argument("--allow-algorithmic-fallback", action="store_true",
                        help="Allow algorithmic (non-DJ-set) fallback. AutoMix-incompatible - off by default.")
    args = parser.parse_args()
    try:
        print(run(args.vibe, n_pairs=args.limit, no_tracklists=args.no_tracklists,
                  allow_algorithmic_fallback=args.allow_algorithmic_fallback))
        return 0
    except ValueError as e:
        print(f"❌ לא הצלחתי לפענח את הוייב - נסה ניסוח אחר ({e})", file=sys.stderr)
        return 1
    except NoDJSetSourceError as e:
        print(f"❌ אין מקור DJ-set: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"❌ נכשל: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
