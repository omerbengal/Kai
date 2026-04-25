#!/usr/bin/env python3
"""
Israeli Railways train-search helper.

Given a from-station, to-station, date and time, returns 3 train routes
(one before the requested time, the matching/target, and one after) as JSON
on stdout.

Usage:
    python train_search.py --from "תל אביב סבידור" --to "הרצליה" \
        --date 2026-04-21 --time 10:00 [--type departure|arrival]

Stations may be passed by name (Hebrew/English/Russian/Arabic — fuzzy-matched
against scripts/train_stations.py STATION_INDEX) or by 4-digit station code.

The script calls the rail.co.il public timetable API directly
(POST https://rail-api.rail.co.il/rjpa/api/v1/timetable/searchTrain).
That endpoint returns the entire day's `travels` array plus an
`onFocusIndex` pointing to the first travel that matches the requested time.
We slice [onFocusIndex-1, onFocusIndex, onFocusIndex+1] to get
before / target / after.

A `--use-playwright` flag is supported as a fallback (in case the API
key changes or the endpoint goes away). It opens the rail.co.il search
page, dismisses popups, and scrapes train cards from the DOM.
See docs/train_search.md for details.

Output: a single JSON object on stdout. On error: {"status":"error","message":"..."}.
"""

import argparse
import io
import json
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# Force UTF-8 stdout on Windows so Hebrew prints correctly
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# ---------------------------------------------------------------------------
# Station table
# ---------------------------------------------------------------------------
sys.path.insert(0, "D:/Kai/scripts")
try:
    from train_stations import STATIONS, STATION_INDEX
except Exception as e:  # pragma: no cover - import failure is fatal
    print(json.dumps({
        "status": "error",
        "message": f"failed to import train_stations: {e}",
    }, ensure_ascii=False))
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
API_URL = "https://rail-api.rail.co.il/rjpa/api/v1/timetable/searchTrain"
API_SUBSCRIPTION_KEY = __import__("os").environ.get("ISRAEL_RAIL_API_KEY", "")  # public key, baked into rail.co.il JS

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)

# Israel timezone is UTC+3 during DST (March-October) and UTC+2 standard
IL_TZ = timezone(timedelta(hours=3))


# ---------------------------------------------------------------------------
# Station resolution
# ---------------------------------------------------------------------------
_PUNCT_RE = re.compile(r"[\-\u2013\u2014\u2010,'\"`()\.\\/]")
_SPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.
    Mirrors the normalisation used when STATION_INDEX was generated.
    """
    if not text:
        return ""
    text = text.strip().lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text)
    return text.strip()


def resolve_station(query: str):
    """
    Return (code:str, canonical_name:str) for a station query, or
    (None, None) if no match.

    Accepts a 4-digit numeric code OR a name (Hebrew/English/etc).
    """
    if query is None:
        return None, None

    q = str(query).strip()
    if q.isdigit() and q in STATIONS:
        return q, STATIONS[q].get("Heb") or q

    norm = _normalize(q)
    if not norm:
        return None, None

    # 1) Exact normalized match
    if norm in STATION_INDEX:
        code = STATION_INDEX[norm]
        return code, STATIONS.get(code, {}).get("Heb", code)

    # 2) Substring match — query is contained in a key, OR a key is contained in query
    candidates = []
    for key, code in STATION_INDEX.items():
        if norm in key or key in norm:
            candidates.append((len(key), key, code))

    if candidates:
        # Prefer the shortest key that contains the query (most specific)
        candidates.sort(key=lambda x: (abs(len(x[1]) - len(norm)), x[0]))
        code = candidates[0][2]
        return code, STATIONS.get(code, {}).get("Heb", code)

    # 3) Token-overlap fallback — split words and look for a key with all query tokens
    tokens = [t for t in norm.split() if len(t) > 1]
    if tokens:
        best = None  # (matches, -keylen, key, code)
        for key, code in STATION_INDEX.items():
            matches = sum(1 for t in tokens if t in key)
            if matches == len(tokens) and matches > 0:
                score = (matches, -len(key))
                if best is None or score > best[:2]:
                    best = (matches, -len(key), key, code)
        if best:
            code = best[3]
            return code, STATIONS.get(code, {}).get("Heb", code)

    return None, None


# ---------------------------------------------------------------------------
# API call (primary path)
# ---------------------------------------------------------------------------
def fetch_via_api(from_code: str, to_code: str, date: str, time: str,
                  schedule_type: str) -> dict:
    """
    POST to the rail.co.il timetable API and return the parsed JSON dict.

    schedule_type: "departure" or "arrival"
    """
    body = {
        "methodName": "searchTrainLuzForDateTime",
        "fromStation": int(from_code),
        "toStation": int(to_code),
        "date": date,
        "hour": time,
        "systemType": "2",
        "scheduleType": "ByDeparture" if schedule_type == "departure" else "ByArrival",
        "languageId": "Hebrew",
        "requestLocation": "{\"latitude\":\"0.0\",\"longitude\":\"0.0\"}",
        "requestIP": "0.0.0.0",
        "userAgent": USER_AGENT,
        "screenResolution": "{\"height\":1440,\"width\":2560}",
        "searchFromFavorites": False,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        method="POST",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "ocp-apim-subscription-key": API_SUBSCRIPTION_KEY,
            "User-Agent": USER_AGENT,
            "Referer": "https://www.rail.co.il/",
            "Origin": "https://www.rail.co.il",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _fmt_time(iso_dt: str) -> str:
    """Convert ISO datetime ('2026-04-21T07:48:00') to 'HH:MM'."""
    if not iso_dt:
        return ""
    try:
        return iso_dt.split("T")[1][:5]
    except Exception:
        return iso_dt


def _station_name(code) -> str:
    code = str(code)
    info = STATIONS.get(code) or {}
    return info.get("Heb") or code


def _legs_from_travel(travel: dict) -> list:
    """Convert API travel object → list of legs in our public schema."""
    legs = []
    for t in travel.get("trains", []) or []:
        legs.append({
            "train_number": str(t.get("trainNumber", "")),
            "platform": str(t.get("originPlatform", "")) or None,
            "dep_station": _station_name(t.get("orignStation")),
            "dep_station_code": str(t.get("orignStation", "")),
            "dep_time": _fmt_time(t.get("departureTime", "")),
            "arr_station": _station_name(t.get("destinationStation")),
            "arr_station_code": str(t.get("destinationStation", "")),
            "arr_time": _fmt_time(t.get("arrivalTime", "")),
            "dest_platform": str(t.get("destPlatform", "")) or None,
        })
    return legs


def build_routes(api_resp: dict) -> list:
    """
    From a full API response, return [before, target, after] (each an entry
    with 'position' and 'legs'). Missing edges are skipped.
    """
    result = api_resp.get("result") or {}
    travels = result.get("travels") or []
    if not travels:
        return []

    on_focus = result.get("onFocusIndex")
    # Defensive: if onFocusIndex is missing or out of range, fall back to 0
    if on_focus is None or on_focus < 0 or on_focus >= len(travels):
        on_focus = 0

    output = []
    if on_focus - 1 >= 0:
        output.append({
            "position": "before",
            "legs": _legs_from_travel(travels[on_focus - 1]),
        })
    output.append({
        "position": "target",
        "legs": _legs_from_travel(travels[on_focus]),
    })
    if on_focus + 1 < len(travels):
        output.append({
            "position": "after",
            "legs": _legs_from_travel(travels[on_focus + 1]),
        })
    return output


# ---------------------------------------------------------------------------
# Playwright fallback (optional)
# ---------------------------------------------------------------------------
def fetch_via_playwright(from_code: str, to_code: str, date: str, time: str,
                         schedule_type: str, headless: bool) -> list:
    """
    Open the rail.co.il search-results page, dismiss popups, scrape DOM.
    Returns the same [before/target/after] structure as build_routes().

    Note: this path does NOT capture per-leg detail for routes with transfers
    (the DOM only shows origin/dest summary; inner legs are only revealed in
    a separate modal). Use the API path for multi-leg routes.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "playwright not installed. Run: pip install playwright && "
            "playwright install chromium"
        ) from e

    hh, mm = time.split(":")
    url = (
        f"https://www.rail.co.il/?page=routePlanSearchResults"
        f"&fromStation={from_code}&toStation={to_code}"
        f"&date={date}&hours={hh}&minutes={mm}"
        f"&scheduleType={'1' if schedule_type == 'departure' else '2'}"
    )
    requested_minutes = int(hh) * 60 + int(mm)

    rows = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(locale="he-IL")
        page = context.new_page()
        page.goto(url, timeout=30000)
        page.wait_for_timeout(5000)  # let CF challenge clear

        # Dismiss any visible dialogs (cookies, station info, service notices)
        page.evaluate("""
            () => {
                const dialogs = Array.from(document.querySelectorAll('[role=\"dialog\"]'));
                dialogs.forEach(d => {
                    const ok = Array.from(d.querySelectorAll('button')).find(b =>
                        ['אישור', 'הבנתי', 'OK'].includes(b.textContent.trim()));
                    if (ok) ok.click();
                });
                const cookie = Array.from(document.querySelectorAll('button'))
                    .find(b => b.textContent.trim() === 'הבנתי');
                if (cookie) cookie.click();
            }
        """)
        page.wait_for_timeout(800)

        rows = page.evaluate("""
            () => {
                const radios = document.querySelectorAll('[role=\"radio\"]');
                const out = [];
                radios.forEach(r => {
                    const aria = r.getAttribute('aria-label') || '';
                    const texts = [];
                    r.querySelectorAll('*').forEach(el => {
                        el.childNodes.forEach(n => {
                            if (n.nodeType === 3 && n.textContent.trim()) {
                                texts.push(n.textContent.trim());
                            }
                        });
                    });
                    out.push({aria, texts});
                });
                return out;
            }
        """)
        browser.close()

    parsed = []
    for r in rows:
        aria = r.get("aria", "")
        texts = r.get("texts", [])
        m_train = re.search(r"רכבת מספר (\d+)", aria)
        m_platform = re.search(r"מרציף (\d+)", aria)
        m_time = re.search(r"בשעה (\d{1,2}:\d{2})", aria)
        m_xfer = re.search(r"מספר החלפות:\s*(\d+)", aria)
        if not (m_train and m_time):
            continue
        dep_time = m_time.group(1)
        # Find arrival time and arrival platform: the texts list contains
        # [trainNum, ?freeSeatsMsg, dep_time, "רציף N", duration, transfers, arr_time, "רציף M", ...]
        time_re = re.compile(r"^\d{1,2}:\d{2}$")
        plat_re = re.compile(r"^רציף\s*(\d+)$")
        time_idxs = [i for i, t in enumerate(texts) if time_re.match(t)]
        arr_time = texts[time_idxs[1]] if len(time_idxs) >= 2 else ""
        arr_platform_match = None
        if arr_time:
            for t in texts[time_idxs[1]:]:
                pm = plat_re.match(t)
                if pm:
                    arr_platform_match = pm.group(1)
                    break
        leg = {
            "train_number": m_train.group(1),
            "platform": m_platform.group(1) if m_platform else None,
            "dep_station_code": str(from_code),
            "dep_station": _station_name(from_code),
            "dep_time": dep_time,
            "arr_station_code": str(to_code),
            "arr_station": _station_name(to_code),
            "arr_time": arr_time,
            "dest_platform": arr_platform_match,
        }
        # Mark legs needing transfer expansion
        n_xfer = int(m_xfer.group(1)) if m_xfer else 0
        parsed.append({"dep_minutes": _to_minutes(dep_time), "n_xfer": n_xfer, "leg": leg})

    if not parsed:
        return []

    # Find target — first row with dep_minutes >= requested
    target_idx = next((i for i, p in enumerate(parsed)
                       if p["dep_minutes"] >= requested_minutes), len(parsed) - 1)

    output = []
    if target_idx - 1 >= 0:
        output.append({"position": "before", "legs": [parsed[target_idx - 1]["leg"]]})
    output.append({"position": "target", "legs": [parsed[target_idx]["leg"]]})
    if target_idx + 1 < len(parsed):
        output.append({"position": "after", "legs": [parsed[target_idx + 1]["leg"]]})
    return output


def _to_minutes(hhmm: str) -> int:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return -1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv):
    p = argparse.ArgumentParser(description="Search Israeli Railways trains.")
    p.add_argument("--from", dest="from_station", required=True,
                   help="Origin station: name (any language) or 4-digit code")
    p.add_argument("--to", dest="to_station", required=True,
                   help="Destination station: name or 4-digit code")
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--time", required=True, help="HH:MM (24h)")
    p.add_argument("--type", choices=("departure", "arrival"), default="departure",
                   help="Whether --time is the desired departure or arrival time")
    p.add_argument("--use-playwright", action="store_true",
                   help="Use the Playwright DOM-scrape fallback instead of the API")
    p.add_argument("--headless", dest="headless", action="store_true", default=True,
                   help="Playwright fallback: run browser headless (default)")
    p.add_argument("--headed", dest="headless", action="store_false",
                   help="Playwright fallback: show browser window for debugging")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    # Resolve stations
    from_code, from_name = resolve_station(args.from_station)
    if not from_code:
        print(json.dumps({
            "status": "error",
            "message": f"לא הצלחתי לזהות את תחנת המוצא: '{args.from_station}'",
        }, ensure_ascii=False))
        return 2

    to_code, to_name = resolve_station(args.to_station)
    if not to_code:
        print(json.dumps({
            "status": "error",
            "message": f"לא הצלחתי לזהות את תחנת היעד: '{args.to_station}'",
        }, ensure_ascii=False))
        return 2

    if from_code == to_code:
        print(json.dumps({
            "status": "error",
            "message": "תחנת מוצא ויעד זהות",
        }, ensure_ascii=False))
        return 2

    # Validate date/time
    try:
        datetime.strptime(args.date, "%Y-%m-%d")
        datetime.strptime(args.time, "%H:%M")
    except ValueError as e:
        print(json.dumps({
            "status": "error",
            "message": f"פורמט תאריך/שעה לא תקין: {e}",
        }, ensure_ascii=False))
        return 2

    # Fetch
    try:
        if args.use_playwright:
            routes = fetch_via_playwright(
                from_code, to_code, args.date, args.time,
                args.type, args.headless,
            )
        else:
            api_resp = fetch_via_api(
                from_code, to_code, args.date, args.time, args.type,
            )
            if api_resp.get("statusCode") != 200:
                msgs = api_resp.get("errorMessages")
                print(json.dumps({
                    "status": "error",
                    "message": f"API responded statusCode={api_resp.get('statusCode')} errors={msgs}",
                }, ensure_ascii=False))
                return 1
            routes = build_routes(api_resp)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        print(json.dumps({
            "status": "error",
            "message": f"HTTP {e.code} from rail API: {body}",
        }, ensure_ascii=False))
        return 1
    except Exception as e:
        print(json.dumps({
            "status": "error",
            "message": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        return 1

    if not routes:
        print(json.dumps({
            "status": "error",
            "message": "לא נמצאו רכבות בזמן המבוקש",
            "from": {"code": from_code, "name": from_name},
            "to": {"code": to_code, "name": to_name},
            "requested_time": f"{args.date} {args.time}",
            "schedule_type": args.type,
        }, ensure_ascii=False))
        return 0

    out = {
        "status": "ok",
        "from": {"code": from_code, "name": from_name},
        "to": {"code": to_code, "name": to_name},
        "requested_time": f"{args.date} {args.time}",
        "schedule_type": args.type,
        "routes": routes,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
