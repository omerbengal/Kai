# Music Pair Suggestions — Design Spec

**Date:** 2026-05-30
**Author:** Kai (as Omer's proxy — Omer offline during brainstorm)
**Status:** Approved (proxy-locked per task brief)

## Problem

Omer DJs informal sets via Spotify's AutoMix (which crossfades transitions automatically) and wants AI-curated song-pair suggestions sorted by vibe. Today he searches manually: this is slow and shallow. He wants to send Kai a one-line vibe description in Hebrew and get back ~10 well-reasoned pair suggestions with the data he needs (BPM, key, Spotify link) to drop them into a playlist in 30 seconds.

## User Story

> Omer (Telegram) → Kai: `שילובי שירים: סט אחרי-חצות אפרו האוס לקראת קיץ`
> Kai (Telegram, ~30s later) → 10 pairs with BPM, Camelot key, one-line "why", Spotify link each.

He copy-pastes the picks into a Spotify playlist. AutoMix does the crossfade.

## Scope (MVP)

**In scope:**
- Hebrew trigger phrase `שילובי שירים:` (with the colon) at the start of a Telegram message → spawn worker.
- LLM-driven parsing of free-form vibe → structured filters (genre, BPM range, energy curve, mood).
- Pair sourcing: (1) 1001Tracklists for real DJ transitions; (2) fallback to BPM+Camelot-key scoring from Tunebat / GetSongBPM.
- Spotify Web API: search for each track, attach the first matching link. If search fails → omit link, keep pair.
- Output: ~10 pairs in a single Telegram reply.
- File-based JSON cache for Tunebat lookups (1 file per `artist__track` slug). No TTL.
- Worker agent (`agents/music_pairs.md`) with standard ACK lifecycle.
- Dispatcher entry in `agents/listener.md`.

**Out of scope (explicitly deferred):**
- Thumbs-up/down feedback storage.
- Multi-user support.
- Audio-features (deprecated by Spotify in 2024 — not needed).
- Auto-creating Spotify playlists.
- Multiple-platform sourcing beyond Spotify links.
- Mixed-language input (Hebrew-only triggers for now; English vibes inside the string are fine since the LLM handles them).

## Non-Functional Requirements

- **Latency:** First ACK edit within seconds; final reply within ~60s typical, ~120s worst-case. The worker emits ACK edits between stages so Omer sees progress.
- **Reliability:** If 1001Tracklists has no data, fall back to scoring. If both fail → reply "no good pairs found, try a different vibe" — never silently empty.
- **Privacy / cost:** No user data leaves Kai except the vibe string (sent to Groq LLM) and track-name searches (sent to Spotify / Tunebat). All within Kai's existing trust model.
- **Hebrew/RTL:** Final reply uses RLM-prefixed lines per Kai convention. Arrow uses `←` for RTL flow.

## Architecture

```
Telegram message starting "שילובי שירים:"
    │
    ▼
listener.md  (dispatcher)
    │  Option F — music pairs (new)
    │  spawn worker with music_pairs.md as prompt
    ▼
agents/music_pairs.md  (worker — owns ACK lifecycle)
    │
    │ runs:
    ▼
scripts/music_pairs/suggest.py <vibe>
    │
    ├── parse_vibe.py     → Groq llama-3.3-70b → {genre, bpm_range, energy_curve, mood}
    │
    ├── source_tracklists.py
    │     └── 1001Tracklists query (via pyTracklists OR scrape) → candidate transitions
    │
    ├── source_scored.py  (fallback / supplement)
    │     ├── seed candidate tracks from Groq (top 30 for the parsed filters)
    │     ├── lookup_tunebat.py (cached) → BPM + key per track
    │     └── score_pairs.py → Camelot-wheel adjacency + BPM proximity
    │
    ├── spotify_search.py → Spotify Web API token + track search per pair → link
    │
    ├── compose_reasons.py → Groq llama-3.3-70b → 1-line "why" per pair (Hebrew)
    │
    └── format_message.py → final Hebrew RTL message text (RLM, ←)
                                                                                ▲
                                                                                │
                                                                       stdout to worker
                                                                                │
worker sends Telegram reply ──────────────────────────────────────────────────────
```

## Components

### 1. `scripts/music_pairs/parse_vibe.py`
- **Input:** raw Hebrew vibe string (with or without the `שילובי שירים:` prefix; strip the prefix if present).
- **Output (JSON):** `{ "genres": ["afro house", "deep house"], "bpm_min": 118, "bpm_max": 124, "energy_curve": "warmup→peak", "mood": ["nocturnal", "summer", "groovy"], "language": "en" }`
- **Implementation:** single Groq chat completion (`llama-3.3-70b-versatile`, temperature 0.3) with a Hebrew-aware system prompt that returns strict JSON. The function strips Markdown fences if the model wraps the JSON.
- **Test:** lock 4–5 example vibe strings to expected parsed JSON (golden tests, mocking the HTTP call).

### 2. `scripts/music_pairs/source_tracklists.py`
- **Input:** parsed filters.
- **Output:** list of `(track_a, track_b, source_url)` candidates from real DJ sets.
- **Implementation:** try `pyTracklists` if installable (`pip install pytracklists` — verify availability during plan execution). If unavailable or no results → return `[]` (forces fallback).
- **No live calls in tests:** record a fixture once, replay with `responses` / monkeypatched `requests.get`.

### 3. `scripts/music_pairs/source_scored.py`
- **Input:** parsed filters.
- **Output:** list of `(track_a, track_b, score, why_data)` from scored candidate generation.
- **Implementation:**
  1. Ask Groq to generate ~30 candidate tracks matching the filters (artist + title only).
  2. For each candidate, call `lookup_tunebat.py` (cached) to get `{bpm, camelot_key}`.
  3. Build pairs via `score_pairs.py`. Keep top ~10.
- **Test:** mock the Groq response and Tunebat lookups; assert scoring picks the expected pairs.

### 4. `scripts/music_pairs/lookup_tunebat.py`
- **Input:** `artist`, `title`.
- **Output:** `{bpm: int, camelot_key: str}` or `None` on miss.
- **Cache:** `scripts/music_pairs/cache/<slug>.json` where `slug = re.sub(r'[^a-z0-9]+', '_', f"{artist}__{title}".lower())`. No TTL.
- **Implementation:** try Tunebat first; if scraping is brittle, fall back to GetSongBPM (free tier API). On both miss → return `None` (pair becomes ineligible).
- **Test:** read fixture JSON, assert cache write/read works.

### 5. `scripts/music_pairs/score_pairs.py`
- **Pure function** — no I/O — easiest to TDD.
- **Input:** `list[{artist, title, bpm, camelot_key}]`, `target_bpm_range`, optional `energy_curve`.
- **Output:** top-N `[(track_a, track_b, score)]`.
- **Scoring (0–100):**
  - BPM compatibility: `100 - abs(bpm_a - bpm_b) * 5` (clamped to 0).
  - Harmonic compatibility (Camelot wheel):
    - Same key: +30
    - Adjacent on wheel (±1, or relative major/minor — same number, swap A/B): +20
    - Two apart: +5
    - Else: 0
  - Energy: if `energy_curve == "warmup→peak"` and `bpm_b > bpm_a`: +10.
- **Test:** golden cases — same key + same BPM = max score; opposite keys + 20-BPM gap = near zero.

### 6. `scripts/music_pairs/spotify_search.py`
- **Input:** `artist`, `title`.
- **Output:** Spotify track URL or `None`.
- **Implementation:** client credentials flow — needs `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET` env vars. Cache the bearer token for its TTL (~1h) in a single file `cache/_spotify_token.json`.
- **Failure mode:** if env vars missing or request fails → return `None` (pair sent without link, with a small footnote in the final message). Worker also logs a one-line warning to `action_log`.
- **Test:** mock HTTP; assert URL extraction from API response.

### 7. `scripts/music_pairs/compose_reasons.py`
- **Input:** pairs + parsed vibe.
- **Output:** dict `{pair_id: hebrew_one_liner}` (≤ 12 Hebrew words each).
- **Implementation:** one Groq call, JSON output, all pairs in one prompt. Hebrew with Kai's voice (concise, no fluff).
- **Test:** mock; verify the prompt mentions every pair, output keys match.

### 8. `scripts/music_pairs/format_message.py`
- **Pure function.** Builds the final Hebrew RTL message.
- **Output format (per pair):**
  ```
  ‏N. {Track A} ← {Track B}
  ‏   {bpm_a}→{bpm_b} BPM · {key_a}→{key_b} · {hebrew_one_liner}
  ‏   🔗 {spotify_link_a}  |  {spotify_link_b}
  ```
  (Note: inside a single-language Hebrew line, `←` is correct per Kai convention; track names are English so they render LTR inside the RTL line, which is what we want.)
- **Header:** `‏🎧 שילובי שירים — {parsed_vibe_summary}\n`
- **Footer (only if any link missing):** `‏(הקישורים החסרים = Spotify search לא הצליח)`

### 9. `scripts/music_pairs/suggest.py`
- **CLI entry point.** Args: `--vibe "<text>"`, `--limit 10`, optional `--no-tracklists` (test bypass).
- Orchestrates 1–8, prints final message to stdout. Returns exit code 0 on success, 1 on hard failure.
- Logs each stage to stderr (e.g., `[parse_vibe] ok`, `[source_tracklists] 0 results — falling back`).

### 10. `agents/music_pairs.md` (worker)
- Standard worker template (mirror `tiktok_news.md` closely).
- Pipeline: edit ACK → run `suggest.py` → on success: stop typing + send reply (with RLM-prefixed text) + delete ACK + log_ack. On error: stop typing + send fail message + delete ACK.
- ACK transitions:
  - `🔄 מחפש שילובי שירים — ⚙️ agent מוכן, מתחיל...`
  - `🔄 מחפש שילובי שירים — 🧠 מנתח וייב...`
  - `🔄 מחפש שילובי שירים — 🎚️ מחפש שילובים מ-DJ sets...`
  - `🔄 מחפש שילובי שירים — 🔑 מתאים BPM ו-key...`
  - `🔄 מחפש שילובי שירים — 🔗 שולף קישורים מ-Spotify...`
  - `🔄 מחפש שילובי שירים — ✍️ כותב את הסיבות...`
  - `🔄 מחפש שילובי שירים — ✅ מוכן, שולח...`
- The worker invokes `suggest.py` in **one** shot and intersperses these edits with stage detection from stderr lines (regex match on `[stage] ok`). Worst case: only the first and last edits fire — that's fine.

### 11. Dispatcher entry in `agents/listener.md`
- New **Option F — Music Pairs:** if message text starts with `שילובי שירים:` (after stripping whitespace), spawn worker with `agents/music_pairs.md`. Standard ACK = `🔄 מחפש שילובי שירים — ⏳ מייצר agent...`. Pass `VIBE_TEXT` = the message minus the prefix.
- Add to the Option summary table.

## Data Flow

1. Telegram message arrives → dispatcher matches `^שילובי שירים:` → spawns worker.
2. Worker writes ACK_MSG_ID, runs `python D:/Kai/scripts/music_pairs/suggest.py --vibe "<text>"`.
3. `suggest.py` runs the pipeline (parse → source → score → spotify → compose → format).
4. Worker captures stdout, prepends RLM if needed (script already does it), sends as Telegram reply.
5. Worker logs to `action_log`: `('music_pairs', 'suggest', '<vibe summary> — <N pairs sent>')`.

## Error Handling

| Failure | Behavior |
|---|---|
| Vibe parse fails (Groq error or non-JSON) | 1 retry, then send: `❌ לא הצלחתי לפענח את הוייב — נסה ניסוח אחר` |
| 1001Tracklists 0 results | Silent fallback to scored sourcing |
| Both sources < 3 pairs | Send: `🤔 לא מצאתי שילובים טובים — נסה וייב אחר (יותר ספציפי לז'אנר/BPM)` |
| Spotify search miss for some tracks | Omit the link, keep the pair, add footer |
| Spotify env vars missing | Skip all Spotify calls, log warning, all pairs sent without links |
| Tunebat miss for a candidate | Drop that candidate from scoring pool |
| Hard crash | Worker's standard error path: send `❌ נכשל: <stderr summary>` |

## Caching

- **Tunebat:** `scripts/music_pairs/cache/<artist_title_slug>.json` — `{bpm, camelot_key, fetched_at_utc}`. No TTL.
- **Spotify token:** `scripts/music_pairs/cache/_spotify_token.json` — `{access_token, expires_at_utc}`.
- **Groq parse / compose:** no caching (vibe-specific, low value).

## Configuration / Secrets

Reads from environment:
- `GROQ_API_KEY` — already set system-wide.
- `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` — **NOT yet configured.** Plan must include: (a) detect missing vars at startup and warn cleanly, (b) document setup in the agent's header comment. Feature degrades gracefully without them (no links).

## Testing

Per superpowers TDD rules:

- **Pure functions get unit tests first** (RED → GREEN → REFACTOR):
  - `score_pairs.py` — full coverage with golden cases.
  - `format_message.py` — Hebrew RTL output, link-missing footer, single-pair edge case.
  - Vibe slug helper, Camelot adjacency helper.
- **I/O-heavy functions use recorded fixtures + monkeypatched HTTP** (`responses` library):
  - `parse_vibe.py` — 4–5 golden vibe→JSON pairs.
  - `lookup_tunebat.py` — cache hit + cache miss + cache write.
  - `spotify_search.py` — token fetch + track search + 404 handling.
  - `compose_reasons.py` — prompt structure + JSON parse.
- **Smoke test for `suggest.py`** — full pipeline with everything mocked, asserts the final stdout matches the expected format.
- **No live network in tests.** Live calls only via the worker on real Telegram messages.

## Open Questions (resolved by proxy per brief)

| Question | Resolution |
|---|---|
| Anthropic vs Groq for LLM parsing? | **Groq** (matches Kai's existing pattern — tiktok_summary.py uses `llama-3.3-70b-versatile`). `ANTHROPIC_API_KEY` is not configured; `GROQ_API_KEY` is. |
| `pyTracklists` actually exists / works? | **Verify during plan execution.** If it fails to install or returns nothing useful, the fallback path is good enough for MVP. Don't block the feature on it. |
| Spotify creds setup? | **Degrade gracefully** if missing. Plan includes a one-line note in the implementation about how to set them up. Not a blocker for first version. |

## Success Criteria

1. Sending `שילובי שירים: סט אחרי-חצות אפרו האוס לקראת קיץ` to Kai produces a Telegram reply with ≥ 8 pairs within 90 seconds.
2. Each pair includes BPM, key, one-line Hebrew "why".
3. ≥ 70% of pairs have working Spotify links (when Spotify creds are configured).
4. All unit tests pass (`pytest D:/Kai/scripts/music_pairs/tests/`).
5. A second message with a different vibe (e.g. `שילובי שירים: בוקר רגוע ג'אז אלקטרוני`) produces a noticeably different pair set.
