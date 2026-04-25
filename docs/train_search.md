# train_search.py — Israeli Railways train finder

`scripts/train_search.py` answers questions like "מתי הרכבת מ-X ל-Y ב-HH:MM?" by
querying rail.co.il's public timetable API and returning 3 candidate routes
(one before the requested time, the matching/target one, and one after) as
JSON on stdout.

## When Kai should use this

Trigger whenever Omer asks about train schedules. Examples:

- "מצא לי רכבת מתל אביב סבידור לחיפה מחר ב-10:00"
- "רכבת ללוד ב-14:30"
- "מתי רכבת מחולון לירושלים ב-09:00?"
- "מתי הרכבת האחרונה מנמל התעופה לסבידור?"
- "רכבת בשעה 19:00 לבאר שבע"

Default mode is **departure** (the user's time = when they want to leave). Use
`--type arrival` only when the user explicitly says they want to arrive by a
certain time (e.g. "להיות בירושלים עד 09:00").

## Usage

```
python scripts/train_search.py \
    --from "תל אביב סבידור" \
    --to   "הרצליה" \
    --date 2026-04-21 \
    --time 10:00 \
    [--type departure|arrival]
```

You can pass either a station **name** (Hebrew, English, Russian, Arabic — any
of the four entries in `STATIONS`) or a **4-digit station code**. Numeric
input is auto-detected as a code; everything else goes through fuzzy match.

Additional flags:

| Flag | Default | Meaning |
|------|---------|---------|
| `--type departure` | yes | `--time` is the desired departure time |
| `--type arrival`   |     | `--time` is the desired arrival time |
| `--use-playwright` | off | Use the DOM-scrape fallback instead of the API (slow, less detail for transfers — only use if API breaks) |
| `--headless`/`--headed` | headless | Playwright-only: show browser window for debugging |

## Output

Successful response (one JSON object on stdout):

```json
{
  "status": "ok",
  "from": {"code": "2300", "name": "חיפה - חוף הכרמל (ש' רזיאל)"},
  "to":   {"code": "7300", "name": "באר שבע- צפון/אוניברסיטה"},
  "requested_time": "2026-04-21 15:00",
  "schedule_type": "departure",
  "routes": [
    {
      "position": "before",
      "legs": [
        {
          "train_number": "119",
          "platform": "2",
          "dep_station": "חיפה - חוף הכרמל (ש' רזיאל)",
          "dep_station_code": "2300",
          "dep_time": "14:36",
          "arr_station": "תל אביב - סבידור מרכז",
          "arr_station_code": "3700",
          "arr_time": "15:29",
          "dest_platform": "3"
        },
        {
          "train_number": "651",
          "platform": "6",
          "dep_station": "תל אביב - סבידור מרכז",
          "dep_station_code": "3700",
          "dep_time": "15:39",
          "arr_station": "באר שבע- צפון/אוניברסיטה",
          "arr_station_code": "7300",
          "arr_time": "17:12",
          "dest_platform": "3"
        }
      ]
    },
    { "position": "target", "legs": [ ... ] },
    { "position": "after",  "legs": [ ... ] }
  ]
}
```

Each route has 1+ legs. Single-leg = direct train. Multi-leg = transfer; each
leg is a separate train and the user changes at the leg-boundary station.

`platform` is the **departure** platform of that leg; `dest_platform` is the
arrival platform (useful when guiding to the next leg's platform).

`routes` may have fewer than 3 entries at the edges of the service day:
e.g. the first train of the day has no `before`, the last has no `after`.
That's intentional — the field is omitted, never set to `null`.

Error response:

```json
{"status": "error", "message": "לא הצלחתי לזהות את תחנת המוצא: 'X'"}
```

Exit codes: `0` = success or "no trains found", `1` = network/API error,
`2` = bad input (unknown station, malformed date/time, same from/to).

## Station resolution

Station data lives in `scripts/train_stations.py`:

- `STATIONS` — code ← `{Heb, Eng, Rus, Arb}` display names
- `STATION_INDEX` — normalised name ← code (one entry per language per station)

Normalisation removes punctuation/dashes and lowercases (so "תל אביב סבידור
מרכז" matches even though `STATIONS` stores it as "תל אביב - סבידור מרכז").
The script's `resolve_station()` tries: exact normalised match ← substring
overlap ← token-overlap fallback. So queries like just "סבידור" or "savidor"
both find Tel Aviv-Savidor.

When Kai builds the `--from`/`--to` arguments from natural language, it can
just pass whatever Omer wrote — no need to normalise or look up codes first.

## Architecture: API vs. Playwright

Two paths exist:

1. **API (default)**: POST to `https://rail-api.rail.co.il/rjpa/api/v1/timetable/searchTrain`
   with a JSON body containing `methodName`, `fromStation`, `toStation`, `date`
   (`YYYY-MM-DD`), `hour` (`HH:MM`), `scheduleType` (`ByDeparture`|`ByArrival`),
   `languageId: "Hebrew"`, `systemType: "2"`, plus a baked-in subscription
   key in the `ocp-apim-subscription-key` header. Returns a single JSON
   document with the **entire day's** travels and an `onFocusIndex` pointing
   to the first travel that matches the requested time. We slice
   `[onFocusIndex-1, onFocusIndex, onFocusIndex+1]` to get before/target/after,
   and each travel's `trains` array gives us per-leg details for free
   (transfers included).

2. **Playwright fallback (`--use-playwright`)**: Opens
   `https://www.rail.co.il/?page=routePlanSearchResults&fromStation=...`,
   waits for the Cloudflare challenge to clear (~5s), dismisses popups, then
   reads `[role="radio"]` train cards from the DOM and parses the
   `aria-label` ("רכבת מספר N, ... מרציף P בשעה HH:MM, מספר החלפות: K").
   Slower (~10s vs <1s for API) and **doesn't capture per-leg data for
   multi-leg routes** — those are only revealed in a separate "הצגת מסלול"
   modal that requires another click sequence. Use only if the API breaks
   (bad subscription key, endpoint moved, etc.).

The API is dramatically better in every dimension: faster, richer data
(stops, free-seat counts, intermediate platforms), no popups, no headless
browser. Keep using it as the default.

## Popups & edge cases observed during dev

When using the Playwright fallback, the rail.co.il page shows several
modal dialogs that can intercept clicks:

| Popup | Trigger | Dismiss |
|-------|---------|---------|
| Cookie consent | Page load | Click button text "הבנתי" |
| Station service notice (e.g. "Haifa Hof HaKarmel side entrance closed") | Specific stations | Click "אישור" inside `[role="dialog"]` |
| Service announcement (electrification, line changes) | Page load | Click "אישור" inside `[role="dialog"]` |

The script's Playwright path dismisses **all** visible dialogs by clicking
any button whose text is one of `אישור / הבנתי / OK`. Re-running this
function is safe — popups won't reappear on the same page load.

Other gotchas:

- The site is React/Cloudflare; first paint shows "Just a moment..." for
  ~3-5 seconds. We wait 5s after navigation before scraping.
- Train cards are `[role="radio"]` not `<tr>` — keep that in mind if the
  site changes.
- Time format on the page is `HH:MM` in 24h regardless of locale; no need
  to localise.

## Extending

**To add a station** (rare — usually new stations are auto-handled by
`STATIONS` if you re-pull):
edit `scripts/train_stations.py`, adding the new code to `STATIONS` AND four
normalised entries to `STATION_INDEX` (one per language). Mirror the format
of an existing entry.

**If the API breaks** (subscription key changes, endpoint moves):
1. Re-discover via Playwright: open the search URL in `mcp__playwright__browser_navigate`,
   then `browser_network_requests --filter searchTrain --requestHeaders --requestBody`.
   The `ocp-apim-subscription-key` header and request body schema will be
   in the captured POST. Update the `API_SUBSCRIPTION_KEY` constant in
   `train_search.py`.
2. As a temporary workaround, pass `--use-playwright` for direct routes.

**If the DOM changes** (Playwright fallback breaks):
re-investigate with `mcp__playwright__browser_snapshot` to find the new
selector. Look for any element that uniquely identifies a train row (the
old one was `[role="radio"]`). Update `fetch_via_playwright()` accordingly.

**To expose intermediate stops** (currently dropped):
the API response's `train.stopStations` array has every interior stop with
arrival/departure time and platform. Extend `_legs_from_travel()` to copy
that data into each leg.

## Testing

Manual smoke tests Kai ran during build (date = 2026-04-21):

| Route | Time | Notes |
|-------|------|-------|
| תל אביב סבידור ← הרצליה | 10:00 | Direct, ~10 min — sanity check |
| תל אביב ההגנה ← ירושלים יצחק נבון | 12:00 | Direct fast line, ~37 min |
| חיפה חוף הכרמל ← באר שבע צפון | 15:00 | Mix of direct + 1-transfer routes via Tel Aviv |
| Code-only: 3700 ← 3500 | 23:55 | Edge of service day; "after" wraps past midnight (00:05) |
| English: "Tel Aviv Savidor" ← "Hertsliya" | 10:00 | Multi-language fuzzy match |
| `--type arrival` mode | — | Returns trains arriving near the requested time |

All pass.
