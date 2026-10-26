#!/usr/bin/env python3
"""
TikTok video → Hebrew news-post composer.

Pipeline (cascade):
1. Resolve canonical URL (`vt.tiktok.com/<short>` → `www.tiktok.com/@user/video/<id>`).
2. Try Supadata API for transcript (canonical URL).
3. On Supadata failure → yt-dlp downloads the video + Groq Whisper.
4. If that fails too → Playwright (capture CDN MP4) + Groq Whisper.
5. Send transcript to Groq for a Hebrew news-post (title + body, no metadata).

Usage:
    python tiktok_summary.py <tiktok_url>
    python tiktok_summary.py <tiktok_url> --raw                # print raw transcript only
    python tiktok_summary.py <tiktok_url> --ack_id N --chat_id ${KAI_TELEGRAM_CHAT_ID} [--orig_id M]
        ^ if --ack_id + --chat_id are given, the script edits the ACK message with
          the composed post directly. Otherwise stdout-only behavior.

stdout: the composed post (title + body) — clean, no extras.
stderr: errors and progress logs.
exit code: 0 on success, 1 on failure (transcript missing or API error).

Setup:
    Set SUPADATA_API_KEY environment variable (or via setx on Windows):
        setx SUPADATA_API_KEY "your_key_here"
    Sign up free at: https://supadata.ai (100 requests/month free tier)
    Playwright fallback requires the venv Python with playwright installed:
        D:/Kai/.venv/Scripts/python.exe -m pip install playwright
        D:/Kai/.venv/Scripts/python.exe -m playwright install chromium
"""

import sys
import os
import re
import argparse
import json
import subprocess

import requests

# --- Config ---
SUPADATA_API_KEY = os.environ.get("SUPADATA_API_KEY", "")
GROQ_API_KEY = os.environ.get(
    "GROQ_API_KEY",
    ""
)
GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

CACHE_DIR = "D:/Kai/tmp"


def log(msg: str) -> None:
    """Progress to stderr (stdout is reserved for the final post)."""
    print(msg, file=sys.stderr, flush=True)


def resolve_canonical_url(url: str) -> str:
    """
    Follow redirects on a short TikTok URL (vt.tiktok.com / vm.tiktok.com)
    to obtain the canonical https://www.tiktok.com/@user/video/<id> form.
    Falls back to the original URL on any failure.
    """
    try:
        resp = requests.head(url, allow_redirects=True, timeout=10)
        canonical = resp.url
        # Strip tracking query params for cleanliness
        canonical = canonical.split("?")[0]
        log(f"Step A: resolved → {canonical}")
        return canonical
    except Exception as e:
        log(f"Step A: HEAD failed ({e}), trying GET fallback...")
        try:
            resp = requests.get(url, allow_redirects=True, timeout=10, stream=True)
            canonical = resp.url.split("?")[0]
            resp.close()
            log(f"Step A: resolved via GET → {canonical}")
            return canonical
        except Exception as e2:
            log(f"Step A: failed entirely ({e2}); using original URL")
            return url


def _supadata_transcript(url: str) -> str:
    """Single Supadata GET. Raises on non-200 or empty transcript."""
    if not SUPADATA_API_KEY:
        raise ValueError("SUPADATA_API_KEY לא מוגדר")

    resp = requests.get(
        "https://api.supadata.ai/v1/transcript",
        params={"url": url},
        headers={"x-api-key": SUPADATA_API_KEY},
        timeout=25,
    )

    if resp.status_code != 200:
        raise ValueError(f"Supadata {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    text = ""
    if "transcript" in data:
        segments = data["transcript"]
        if isinstance(segments, list):
            text = " ".join(item["text"] for item in segments if item.get("text"))
        elif isinstance(segments, str):
            text = segments
    if not text and "content" in data:
        content = data["content"]
        if isinstance(content, list):
            text = " ".join(item["text"] for item in content if isinstance(item, dict) and item.get("text"))
        elif isinstance(content, str):
            text = content
    if not text and "text" in data:
        raw_text = data["text"]
        if isinstance(raw_text, list):
            text = " ".join(item if isinstance(item, str) else item.get("text", "") for item in raw_text if item)
        elif isinstance(raw_text, str):
            text = raw_text

    if not text or not text.strip():
        raise ValueError("Supadata returned empty transcript")
    return text


def _video_id_from_url(url: str) -> str:
    """Extract numeric video id from a canonical TikTok URL, or fall back to a hash."""
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
    return str(abs(hash(url)))[:18]


def _whisper_transcribe(mp4_path: str) -> str:
    """Send an MP4 file to Groq Whisper API (curl, like transcribe.py)."""
    log("Whisper: transcribing...")   # shared by both download paths
    result = subprocess.run(
        [
            "curl", "-s", "-X", "POST", GROQ_WHISPER_URL,
            "-H", f"Authorization: Bearer {GROQ_API_KEY}",
            "-F", f"file=@{mp4_path};type=video/mp4;filename=video.mp4",
            "-F", "model=whisper-large-v3",
            "-F", "language=he",
        ],
        capture_output=True,
        timeout=90,
    )
    try:
        data = json.loads(result.stdout.decode("utf-8"))
    except Exception:
        raise ValueError(f"Whisper non-JSON response: {result.stdout[:300]!r}")
    if "error" in data:
        raise ValueError(f"Whisper error: {data['error'].get('message', data['error'])}")
    return data.get("text", "").strip()


def _playwright_whisper_fallback(canonical_url: str) -> str:
    """
    Use headless Chromium to load the TikTok page, capture the CDN MP4 URL
    from network traffic, download the MP4 with the page cookies, then Whisper.

    Requires playwright to be installed in the calling Python interpreter.
    Reuses cached MP4 in D:/Kai/tmp/tiktok_<id>.mp4 if present.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ValueError(
            "Playwright not installed in this interpreter. "
            "Run via D:/Kai/.venv/Scripts/python.exe (or install playwright "
            "+ chromium browser)."
        ) from e

    video_id = _video_id_from_url(canonical_url)
    mp4_path = os.path.join(CACHE_DIR, f"tiktok_{video_id}.mp4")

    if os.path.exists(mp4_path) and os.path.getsize(mp4_path) > 100_000:
        log(f"Step D.1: cache hit → {mp4_path}")
    else:
        log("Step D.1: launching Playwright to capture CDN URL...")
        os.makedirs(CACHE_DIR, exist_ok=True)
        captured_video_url = None
        cookies_list = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                )
                page = context.new_page()

                video_urls = []

                def on_request(req):
                    u = req.url
                    if "v16-webapp-prime" in u or (
                        "tiktokcdn" in u and ".mp4" in u
                    ):
                        video_urls.append(u)

                page.on("request", on_request)

                page.goto(canonical_url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(4_000)

                # Pick the first plausible MP4 URL
                for u in video_urls:
                    if "mime_type=video_mp4" in u or ".mp4" in u:
                        captured_video_url = u
                        break

                if not captured_video_url:
                    # Try a broader scan via network requests API
                    raise ValueError(
                        f"Playwright did not capture a video URL. "
                        f"Saw {len(video_urls)} candidate(s)."
                    )

                cookies_list = context.cookies()
            finally:
                browser.close()

        # Download MP4 with the captured cookies
        log("Step D.2: downloading MP4 with session cookies...")
        cookies = {c["name"]: c["value"] for c in cookies_list}
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.tiktok.com/",
        }
        r = requests.get(captured_video_url, cookies=cookies, headers=headers, timeout=60, stream=True)
        if r.status_code != 200:
            raise ValueError(f"CDN download failed: status {r.status_code}")
        with open(mp4_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
        log(f"Step D.2: saved {os.path.getsize(mp4_path)} bytes → {mp4_path}")

    transcript = _whisper_transcribe(mp4_path)
    if not transcript.strip():
        raise ValueError("Whisper returned empty transcript")
    return transcript


def _ytdlp_whisper_fallback(canonical_url: str) -> str:
    """
    Download the video with yt-dlp, then Whisper it.

    Added 2026-08-18 after both existing paths failed on a live link: Supadata
    answered 400 "URL did not return media content", and the Playwright capture
    saw 0 candidate MP4 URLs. yt-dlp pulled the same video in about a second,
    because it solves TikTok's JS challenge instead of hoping the CDN request
    shows up in network traffic. It also needs no browser, so it is tried before
    Playwright now.

    Shares the tiktok_<id>.mp4 cache with the Playwright path on purpose - if one
    path already downloaded the video, the other must not download it again.
    """
    video_id = _video_id_from_url(canonical_url)
    mp4_path = os.path.join(CACHE_DIR, f"tiktok_{video_id}.mp4")

    if os.path.exists(mp4_path) and os.path.getsize(mp4_path) > 100_000:
        log(f"Step C.1: cache hit → {mp4_path}")
    else:
        os.makedirs(CACHE_DIR, exist_ok=True)
        log("Step C.1: downloading with yt-dlp...")
        # sys.executable is the venv Python in normal operation; the explicit path
        # is the safety net for a run started with some other interpreter.
        candidates = [sys.executable, "D:/Kai/.venv/Scripts/python.exe"]
        last_err = None
        for interpreter in candidates:
            if not interpreter:
                continue
            try:
                result = subprocess.run(
                    [
                        interpreter, "-m", "yt_dlp",
                        "-f", "ba/b",             # audio-only if offered, else the video
                        "--no-playlist",
                        "-o", mp4_path,
                        canonical_url,
                    ],
                    capture_output=True,
                    timeout=180,
                )
            except Exception as e:
                last_err = e
                continue
            if result.returncode == 0 and os.path.exists(mp4_path):
                break
            last_err = (result.stderr or result.stdout or b"")[-300:]
        if not os.path.exists(mp4_path):
            raise ValueError(f"yt-dlp did not produce a file: {last_err}")
        log(f"Step C.1: saved {os.path.getsize(mp4_path)} bytes → {mp4_path}")

    transcript = _whisper_transcribe(mp4_path)
    if not transcript.strip():
        raise ValueError("Whisper returned empty transcript")
    return transcript


def fetch_oembed(canonical_url: str) -> dict:
    """
    Fetch TikTok oEmbed metadata (creator caption).
    Endpoint: https://www.tiktok.com/oembed?url=<canonical_tiktok_url>
    Returns dict; we only use 'title' (creator caption) downstream.
    On any failure (timeout, non-200, JSON parse), returns {} and logs to stderr.
    NEVER raises — additive enrichment only.
    """
    import urllib.request as _ur
    import urllib.parse as _up
    try:
        url = "https://www.tiktok.com/oembed?url=" + _up.quote(canonical_url, safe=":/@")
        req = _ur.Request(url, headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        })
        with _ur.urlopen(req, timeout=5) as r:
            if r.status != 200:
                log(f"oEmbed: unavailable (status {r.status})")
                return {}
            data = json.loads(r.read().decode("utf-8"))
        title = (data.get("title") or "").strip()
        log(f'oEmbed: title="{title[:120]}"')
        return data
    except Exception as e:
        log(f"oEmbed: unavailable ({e})")
        return {}


def get_transcript(tiktok_url: str) -> tuple[str, str, str]:
    """
    Returns (transcript, source, canonical_url) where source ∈
    {"supadata", "ytdlp_whisper", "playwright_whisper"}.
    Raises ValueError if every path fails.

    Note on the error variables: `except ... as e` unbinds the name when the block
    ends, so the old code raised UnboundLocalError on e_supa while trying to report
    that both paths had failed - the real reason was replaced by a crash. Each
    failure is copied into a plain variable that outlives its block.
    """
    canonical = resolve_canonical_url(tiktok_url)
    failures = []

    # Transcript cache (2026-09-17): re-running the same video (e.g. to get a
    # different format) must not spend another Supadata/Whisper call.
    cache_path = os.path.join(CACHE_DIR, f"tiktok_{_video_id_from_url(canonical)}_transcript.json")
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("text", "").strip():
            log(f"Transcript cache hit -> {cache_path}")
            return cached["text"], cached.get("source", "cache"), canonical
    except Exception:
        pass

    def _remember(text: str, source: str) -> None:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"text": text, "source": source}, f, ensure_ascii=False)
        except Exception as e:
            log(f"Transcript cache: save failed ({e})")

    # Step B — Supadata
    try:
        log("Step B: querying Supadata...")
        text = _supadata_transcript(canonical)
        log("Step B: success")
        _remember(text, "supadata")
        return text, "supadata", canonical
    except Exception as e:
        err = e
        failures.append(f"Supadata: {err}")
        log(f"Step B: failed — {err}")

    # Step C — yt-dlp + Whisper. Ahead of Playwright: no browser to launch, and it
    # handles TikTok's JS challenge rather than depending on a CDN request showing
    # up in captured traffic.
    try:
        log("Step C: starting yt-dlp + Whisper fallback...")
        text = _ytdlp_whisper_fallback(canonical)
        log("Step C: success")
        _remember(text, "ytdlp_whisper")
        return text, "ytdlp_whisper", canonical
    except Exception as e:
        err = e
        failures.append(f"yt-dlp+Whisper: {err}")
        log(f"Step C: failed — {err}")

    # Step D — Playwright + Whisper, the original fallback, kept as last resort.
    try:
        log("Step D: starting Playwright + Whisper fallback...")
        text = _playwright_whisper_fallback(canonical)
        log("Step D: success")
        _remember(text, "playwright_whisper")
        return text, "playwright_whisper", canonical
    except Exception as e:
        err = e
        failures.append(f"Playwright+Whisper: {err}")
        log(f"Step D: failed — {err}")

    raise ValueError("All transcript paths failed. " + " | ".join(failures))


# --- Format variety (2026-09-17) ---------------------------------------------
# Omer (TG 14114, 14117): every post looked identical - 🚀 opener, 🔹 bullets,
# 📊 for data, a question/closing line at the end, and the model even printed the
# skeleton labels ("הקשר", "עובדה 1"). Prompt-level "please vary" was not enough
# (see memory feedback_tiktok_format_variety). Variety is now decided in CODE:
#   1. a structural style is drawn at random per run (never the last two used),
#   2. 3-6 emojis are drawn at random from broad pools (never last run's picks):
#      headline opener + line markers + accent. They are handed to the model and
#      injected in post-processing if it skips them (TG 14120: never zero),
#   3. the ending type is drawn at random (a question ending is rare),
#   4. post-processing strips markdown, section labels, em/en dashes and any
#      emoji that was not handed out, then guarantees the drawn ones appear.
import random
import unicodedata

STYLE_HISTORY_PATH = os.path.join(CACHE_DIR, "tiktok_last_styles.json")

FORMAT_STYLES = [
    {
        "key": "headline_paragraph",
        "instruction": (
            "כותרת חדה בשורה אחת, ואחריה 3-4 פסקאות קצרות של טקסט רציף (כל פסקה משפט או שניים). "
            "בלי רשימה - הכל זורם כמו הודעה לחבר."
        ),
    },
    {
        "key": "question_hook",
        "instruction": (
            "פתח בשאלה מסקרנת שהתוכן עונה עליה (שאלה בפתיחה בלבד, לא בסוף). "
            "אחריה התשובה ב-5-8 שורות קצרות עם הפרטים."
        ),
    },
    {
        "key": "numbered_list",
        "instruction": (
            "שורת פתיחה אחת קצרה, ואז רשימה ממוספרת (1. 2. 3.) של 4-6 נקודות עיקריות, "
            "כל נקודה שורה אחת עם פרט קונקרטי. אחרי הרשימה שורת סיום קצרה אחת."
        ),
    },
    {
        "key": "oneliner_then_lines",
        "instruction": (
            "שורה ראשונה היא משפט אחד שאומר את כל הסיפור. אחריה שורה ריקה, "
            "ואז 5-8 שורות קצרות של פירוט, כל אחת עומדת בפני עצמה."
        ),
    },
    {
        "key": "story",
        "instruction": (
            "סגנון סיפור/אנקדוטה: פתח ברגע או בסיטואציה קונקרטית מהתוכן "
            "(\"דמיינו ש...\" / \"עד אתמול...\" / תיאור מה קרה), והמשך כסיפור של 3-4 פסקאות קצרות. "
            "בלי כותרת נפרדת."
        ),
    },
    {
        "key": "things_to_know",
        "instruction": (
            "שורת פתיחה בנוסח \"X דברים שכדאי לדעת על ...\" (עם המספר האמיתי), "
            "ואז הנקודות, כל אחת בשורה נפרדת שמתחילה באמוג'י הסמן שנבחר."
        ),
    },
    {
        "key": "tldr_then_details",
        "instruction": (
            "פתח ב-\"אמ;לק:\" ומשפט אחד. אחרי שורה ריקה - מי שרוצה להעמיק: "
            "5-7 שורות קצרות של הפרטים המעניינים, טקסט רגיל."
        ),
    },
    {
        "key": "before_after",
        "instruction": (
            "בנה את הפוסט סביב ניגוד של לפני/אחרי (או ישן/חדש) כפי שעולה מהתוכן: "
            "שורת פתיחה, פסקה קצרה על המצב הקודם, 2-3 פסקאות קצרות על מה שמשתנה (עם הפרטים הקונקרטיים), ושורת סיום קצרה. "
            "בלי המילים \"לפני\"/\"אחרי\" ככותרות-משנה."
        ),
    },
]

# Emojis (2026-09-17, TG 14117 + 14120): Omer wants emojis RANDOM, never absent.
# Every post gets 3-6 emojis drawn per run: an opener on the headline, 2-3 line
# markers (paragraph/bullet starts) and 0-1 accent mid-post or at the end.
# None of the previous run's emojis are reused. 🚀 / 🔹 / 📊 / 🚨 / 🤩 are
# deliberately absent - they were the stuck defaults.
OPENER_EMOJI_POOL = [
    "🤯", "👀", "⚡", "🧠", "💡", "🔥", "✨", "🎯", "🛠️", "🧩",
    "📣", "🆕", "🌀", "🔍", "🤖", "💥", "🪄", "🧪", "📌", "🎬",
    "🙌🏻", "😮", "🥊", "🧭", "🗞️", "🔓", "⏱️", "🌍", "💬", "🎉",
    "🦾", "🛸", "🌟", "🏆", "🧬", "🎙️", "🔔", "🪐", "🌋", "🎮",
]
MARKER_EMOJI_POOL = [
    "👉🏻", "🔸", "▪️", "✔️", "💠", "🟢", "🟣", "🟠", "⭐", "🔘",
    "📍", "🧷", "🔑", "🧱", "⚙️", "🪛", "📎", "🏷️", "🔗", "🎈",
    "💎", "🌱", "🍀", "🧊", "🔋", "📝", "🗂️", "🧰", "🎲", "🪙",
    "✅", "☑️", "➕", "🔶", "🟡", "🔺", "🖇️", "🗝️", "🌐", "💾",
]
ACCENT_EMOJI_POOL = [
    "😅", "🍿", "💀", "🫠", "👏🏻", "🤝🏻", "😎", "🙃", "🤔", "🥲",
    "💪🏻", "👌🏻", "😬", "🤷🏻‍♂️", "🧨", "🏁", "🫡", "🔮", "😏", "🙈",
    "🤌🏻", "😂", "🥳", "😱", "🤓", "🫣", "👑", "💯", "🎊", "🥇",
]

# Ending types. "question" is rare on purpose.
ENDING_TYPES = [
    ("fact", "סיים בעובדה הכי מעניינת או מפתיעה - בלי משפט סיכום אחריה.", 4),
    ("punchline", "סיים בפואנטה קצרה ועוקצנית (עד 8 מילים), לא בשאלה.", 3),
    ("aside", "סיים בהערת אגב קלילה בסוגריים, לא בשאלה.", 2),
    ("abrupt", "אין שורת סיום בכלל - הפוסט נגמר בנקודת התוכן האחרונה.", 3),
    ("question", "מותר לסיים בשאלה קצרה אחת לקוראים.", 1),
]

BANNED_LABELS = [
    "הוק", "הקשר", "רקע", "עובדה", "עובדה 1", "עובדה 2", "עובדה 3",
    "פרט זכיר", "פואנטה", "משפט סוגר", "סיכום", "כותרת",
    "עובדה מפתיעה", "עובדה מעניינת", "פרט מעניין", "פרט מפתיע", "השורה התחתונה",
]

_EMOJI_RE = re.compile(
    "(?:[\U0001F1E6-\U0001F1FF]{2}"
    "|[0-9#*]️?⃣"
    "|[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌀-⏿■-◿]"
    "[️\U0001F3FB-\U0001F3FF]*(?:‍[\U0001F000-\U0001FAFF☀-➿♀♂][️\U0001F3FB-\U0001F3FF]*)*)"
)


def _load_style_history() -> dict:
    try:
        with open(STYLE_HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_style_history(hist: dict) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(STYLE_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"style history: save failed ({e})")


def pick_format(rng: random.Random | None = None) -> dict:
    """Randomly choose style, emojis and ending - avoiding recent repeats."""
    rng = rng or random.Random()
    hist = _load_style_history()

    recent_styles = hist.get("recent_styles", [])[-2:]
    styles = [s for s in FORMAT_STYLES if s["key"] not in recent_styles] or FORMAT_STYLES
    style = rng.choice(styles)

    used = set(hist.get("last_emojis", []))

    def _draw(pool: list[str], k: int) -> list[str]:
        fresh = [e for e in pool if e not in used]
        return rng.sample(fresh if len(fresh) >= k else pool, k)

    opener = _draw(OPENER_EMOJI_POOL, 1)[0]
    # Markers: either one marker repeated on every item, or a different one per line.
    marker_mode = rng.choice(["same", "mixed"])
    markers = _draw(MARKER_EMOJI_POOL, 1 if marker_mode == "same" else 3)
    # "same" mode repeats one marker, so it always gets an accent too (>=3 emojis).
    accents = _draw(ACCENT_EMOJI_POOL, 1 if marker_mode == "same" else rng.choice([0, 1, 1]))

    last_ending = hist.get("last_ending")
    endings = [e for e in ENDING_TYPES if e[0] != last_ending]
    ending = rng.choices(endings, weights=[e[2] for e in endings])[0]

    return {
        "style": style,
        "opener": opener,
        "opener_position": rng.choice(["start", "start", "end_of_first_line"]),
        "marker_mode": marker_mode,
        "markers": markers,
        "accents": accents,
        "emojis": [opener] + markers + accents,
        "ending_key": ending[0],
        "ending_instruction": ending[1],
    }


def record_format(fmt: dict) -> None:
    hist = _load_style_history()
    recent = (hist.get("recent_styles", []) + [fmt["style"]["key"]])[-5:]
    hist.update({
        "recent_styles": recent,
        "last_emojis": fmt["emojis"],
        "last_ending": fmt["ending_key"],
    })
    for stale in ("prev_emojis", "last_bullet"):
        hist.pop(stale, None)
    _save_style_history(hist)


def _base_emoji(e: str) -> str:
    """Strip variation selectors / skin tones so '▪️' matches '▪'."""
    return "".join(ch for ch in e if ch != "️" and not ("\U0001F3FB" <= ch <= "\U0001F3FF"))


def _starts_with_emoji(line: str) -> bool:
    # "1. 🪛 text" counts as marked too - the number is not the marker.
    m = _EMOJI_RE.match(re.sub(r"^\s*\d+[.)]\s*", "", line))
    return bool(m)


def _ensure_emojis(text: str, fmt: dict) -> str:
    """Guarantee the drawn emojis appear even if the model ignored them."""
    lines = text.split("\n")
    idx = [i for i, l in enumerate(lines) if l.strip()]
    if not idx:
        return text
    bases_in = lambda s: {_base_emoji(m.group(0)) for m in _EMOJI_RE.finditer(s)}

    # 1. Opener on the headline.
    first = idx[0]
    if _base_emoji(fmt["opener"]) not in bases_in(lines[first]):
        if fmt["opener_position"] == "end_of_first_line":
            lines[first] = lines[first].rstrip() + " " + fmt["opener"]
        else:
            lines[first] = fmt["opener"] + " " + lines[first].lstrip()

    # 2. Markers at the start of body lines.
    body = idx[1:]
    marker_bases = {_base_emoji(m) for m in fmt["markers"]}
    marked = sum(1 for i in body if _starts_with_emoji(lines[i])
                 and (bases_in(lines[i][:6]) & marker_bases))
    want = min(3 if fmt["marker_mode"] == "mixed" else len(body), len(body), 5)
    if len(body) > 3 and fmt["ending_key"] != "fact":
        want = min(want, len(body) - 1)   # leave a punchline/aside line bare
    if marked < min(2, want):
        k = 0
        for i in body:
            if k >= want:
                break
            if _starts_with_emoji(lines[i]):
                continue
            mk = fmt["markers"][(k + marked) % len(fmt["markers"])]
            num = re.match(r"^(\s*\d+[.)]\s*)(.*)$", lines[i])
            lines[i] = (num.group(1) + mk + " " + num.group(2)) if num else (mk + " " + lines[i].lstrip())
            k += 1

    text = "\n".join(lines)

    # 3. Accent: if missing, append to the last non-empty line.
    if fmt["accents"] and _base_emoji(fmt["accents"][0]) not in bases_in(text):
        lines = text.split("\n")
        last = [i for i, l in enumerate(lines) if l.strip()][-1]
        lines[last] = lines[last].rstrip() + " " + fmt["accents"][0]
        text = "\n".join(lines)
    return text


def clean_post(text: str, fmt: dict) -> str:
    """Safety net: plain-text Telegram output, only the drawn emojis, and all of them present."""
    # Markdown bold/italic/headers/code
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.S)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.M)
    # Dashes: em/en dash and non-breaking hyphen -> plain hyphen
    text = text.replace("—", "-").replace("–", "-").replace("‑", "-")

    # Section labels copied from the prompt skeleton: "הקשר -", "עובדה 1:" etc.
    label_alt = "|".join(sorted((re.escape(l) for l in BANNED_LABELS), key=len, reverse=True))
    text = re.sub(
        rf"^(\s*(?:\S{{1,3}}\s+)?)(?:{label_alt})\s*\d*\s*[-:]\s*",
        r"\1",
        text,
        flags=re.M,
    )

    # Emojis: drop anything that was not drawn (keeps the stuck defaults out).
    allowed = {_base_emoji(e) for e in fmt["emojis"]}

    def _filter(m: re.Match) -> str:
        tok = m.group(0)
        base = _base_emoji(tok)
        if base in allowed:
            return tok
        if base in ("↑", "↓", "•", "◦") or (len(base) == 1 and unicodedata.category(base) == "Sm"):
            return tok
        return ""

    text = _EMOJI_RE.sub(_filter, text)
    # Tidy whitespace left behind by removals
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"^[ \t]+", "", text, flags=re.M)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    text = _ensure_emojis(text, fmt)
    # Never the same emoji twice in a row ("🪛 🪛").
    text = re.sub(r"(" + _EMOJI_RE.pattern + r")(?:[ \t]*\1)+", r"\1", text)
    # Transcription garbles the model sometimes leaves in place.
    for bad, good in (("קורורג", "Cowork"), ("קוברק", "Cowork"), ("קו-וורק", "Cowork"),
                      ("גוגל סלייט", "Google Slides"), ("גוגל סליידס", "Google Slides")):
        text = text.replace(bad, good)
    return text


# --- Repeated rhetorical device (2026-10-10, TG 16239) -------------------------
# Omer: "אתה משתמש באלמנט של פתאום פעמיים בהודעה". A hook/effect word may appear
# ONCE per post. Checked in code (prompt-only rules fail, see the variety block
# above): a repeat triggers the rewrite retry, and if the repeat survives it, the
# droppable adverbs are removed deterministically from the 2nd occurrence on.
# Product names and topic nouns are never in these lists, so they cannot trip it.
DROPPABLE_DEVICES = ["פתאום", "לפתע", "בעצם", "תכלס", "ממש", "באמת"]
# Detected (rewrite retry) but never auto-dropped: removing them breaks the sentence.
HOOK_DEVICES = [
    "דמיינו", "תדמיינו", "תארו לכם", "תחשבו על", "שימו לב", "מסתבר", "תקשיבו",
    "מטורף", "מדהים", "משוגע", "הזוי", "לא תאמינו", "עד אתמול", "פשוט",
]
_HEB_PREFIX = r"(?<![א-ת])([ושכלבה]{0,2})"


def _device_re(device: str) -> re.Pattern:
    return re.compile(_HEB_PREFIX + re.escape(device).replace(r"\ ", r"\s+") + r"(?![א-ת])")


def _repeated_devices(text: str) -> dict[str, int]:
    """Rhetorical/filler devices that appear more than once in the post -> count."""
    found = {}
    for device in DROPPABLE_DEVICES + HOOK_DEVICES:
        n = len(_device_re(device).findall(text))
        if n > 1:
            found[device] = n
    return found


def _drop_repeated_devices(text: str) -> str:
    """Last resort: keep the first occurrence of a droppable adverb, remove the rest."""
    for device in DROPPABLE_DEVICES:
        seen = [0]

        def _sub(m: re.Match) -> str:
            seen[0] += 1
            return m.group(0) if seen[0] == 1 else m.group(1)   # keep the ו/ש prefix

        text = re.sub(_device_re(device).pattern + r"[ \t]*", _sub, text)
    return text


# Length bounds in non-empty lines (TG 18811, 23/10/2026: a 4-line post was "too short and compressed";
# memory feedback_tiktok_detail_level = 6-10 lines). Enforced here, so a too-short post gets one retry.
MIN_LINES = 6
MAX_LINES = 12


def _violations(text: str, fmt: dict) -> list[str]:
    problems = []
    for device, n in _repeated_devices(text).items():
        problems.append(f'המילה "{device}" מופיעה {n} פעמים - מותר פעם אחת בלבד בכל הפוסט')
    if re.search(r"\?\s*\S{0,4}\s*$", text) and fmt["ending_key"] != "question":
        problems.append("ends with a question")
    if fmt["ending_key"] != "question" and re.search(r"תנו לי|תגיבו|שתפו|מה דעתכם|ספרו לי|תעדכנו אותי|שלחו לי", text):
        problems.append("call-to-action ending")
    if re.search(r"קיבלתי את השאלה|שאלו אותי|בלייבים|בסדנאות שלי|תעקבו אחריי", text):
        problems.append("creator first-person voice copied")
    n_lines = len([l for l in text.splitlines() if l.strip()])
    if n_lines > MAX_LINES:
        problems.append(f"more than {MAX_LINES} lines")
    if n_lines < MIN_LINES:
        problems.append(f"רק {n_lines} שורות - קצר ומתומצת מדי, צריך לפחות {MIN_LINES} שורות עם הפרטים מהתמלול")
    first = text.splitlines()[0] if text else ""
    if first.startswith("🚀") and "🚀" not in fmt["emojis"]:
        problems.append("rocket opener")
    return problems


def summarize_hebrew(transcript: str, oembed: dict | None = None, fmt: dict | None = None) -> str:
    """Compose a Hebrew post from a transcript using Groq.

    `fmt` (from pick_format) decides structure, emojis and ending. It is
    drawn randomly per run when not supplied. Optional `oembed` may include
    'title' (creator caption) as extra context.
    """
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY לא מוגדר")

    fmt = fmt or pick_format()
    log(f"Format: style={fmt['style']['key']} emojis={fmt['emojis']} "
        f"markers={fmt['marker_mode']} ending={fmt['ending_key']}")

    max_chars = 8000
    if len(transcript) > max_chars:
        transcript = transcript[:max_chars] + "... [קוצר]"

    markers_txt = " ".join(fmt["markers"])
    emoji_rule = (
        f"חובה להשתמש באמוג'ים שנבחרו (ורק בהם): פותח {fmt['opener']} "
        + ("בתחילת השורה הראשונה. " if fmt["opener_position"] == "start" else "בסוף השורה הראשונה. ")
        + (f"סמן {markers_txt} בתחילת כל פריט/פסקה בגוף הפוסט. " if fmt["marker_mode"] == "same"
           else f"סמנים {markers_txt} - אחד שונה בתחילת כל אחת מ-2-3 הפסקאות/הפריטים העיקריים. ")
        + (f"ועוד {fmt['accents'][0]} אחד באמצע הפוסט או בסוף משפט. " if fmt["accents"] else "")
        + "אמוג'י תמיד בתחילת שורה או בסוף משפט - לעולם לא במקום מילה. אסור כל אמוג'י אחר."
    )

    system_prompt = (
        "אתה כותב פוסט בסגנונו של עומר לקבוצת וואטסאפ של חברים שמתעניינים ב-AI וטכנולוגיה. "
        "הפוסט צריך לתפוס את העין בשורה הראשונה ולתת תוכן אמיתי למי שממשיך לקרוא.\n\n"

        "## הפורמט של הפוסט הזה (נבחר מראש - חובה לציית)\n"
        f"מבנה: {fmt['style']['instruction']}\n"
        f"אמוג'ים: {emoji_rule}\n"
        f"סיום: {fmt['ending_instruction']}\n\n"

        "## הקול של עומר\n"
        "- עברית כתובה, ישירה, כמו הודעה לחבר. בלי ז'רגון מנופח ובלי עברית של כתבה בעיתון.\n"
        "- אורך: 6-10 שורות קצרות (לא כולל שורות ריקות), עם שורות ריקות בין חלקים. פחות מ-6 שורות זה תמצות יתר - "
        "עומר רוצה את הפרטים, לא רק את הכותרת. אם יש עומק אמיתי - עד 12. כל עובדה, מספר ודוגמה מעניינים מהתמלול "
        "נכנסים (מה היה קודם, מה השתנה, הדוגמה הקונקרטית ומה יצא ממנה).\n"
        "- התוכן צריך לכלול: מה קרה, למה זה מעניין, 2-3 עובדות קונקרטיות, ופרט אחד שזוכרים. "
        "זו רשימת תוכן, לא תבנית - אסור לכתוב אותה ככותרות או תוויות.\n"
        "- שמות מותג נשארים באנגלית בתוך הטקסט העברי: Claude, Anthropic, OpenAI, ChatGPT, Gemini.\n"
        "- מספרים: בחר 2-4 הכי מעניינים ונסח אותם כמו שחבר היה אומר, לא רשימת מפרט טכני.\n"
        "- הערות אגב בסוגריים הן חלק מהקול, במידה.\n"
        "- כל מילת אפקט או מהלך רטורי (הפתעה, פנייה לקורא, הגזמה) מופיע לכל היותר פעם אחת בפוסט. "
        "אם השתמשת בו בפתיחה - לא חוזרים אליו בהמשך.\n\n"

        "## איסורים\n"
        "- טקסט פשוט בלבד: בלי כוכביות, בלי הדגשות markdown, בלי # ובלי backticks.\n"
        "- אסור לכתוב תוויות מבנה כמו \"הוק\", \"הקשר\", \"רקע\", \"עובדה 1\", \"פואנטה\", \"סיכום\" בתחילת שורה.\n"
        "- אסור קו מפריד ארוך (em dash או en dash). רק מקף רגיל (-).\n"
        "- נאמנות מלאה לתמלול: אל תמציא עובדות, מספרים, אחוזים, תאריכים, משכי זמן, השוואות או מוצרים "
        "שלא הוזכרו בתמלול או בכיתוב היוצר. אם נאמר \"כמעט תמיד\" - כותבים \"כמעט תמיד\", לא אחוז. "
        "השוואה למוצר אחר נכנסת רק אם היא נאמרה במפורש, ובדיוק כפי שנאמרה.\n"
        "- הפוסט נכתב בקול של עומר, לא של היוצר: אל תעתיק גוף ראשון של היוצר (\"קיבלתי את השאלה\", \"אני שמח\").\n"
        "- אסור תוויות עם נקודתיים כמו \"עובדה מפתיעה:\" או \"השורה התחתונה:\" (חוץ מ-\"אמ;לק:\" כשהמבנה מבקש).\n"
        "- אל תכתוב \"הסרטון מסביר\" / \"בקליפ\" / \"לפי הסרטון\", ואל תזכיר יוצר, פלטפורמה או קישור.\n"
        "- אל תסיים ב\"בקיצור\" / \"לסיכום\" / \"המסר הוא\", ואל תפתח ב\"היי חברים\".\n"
        "- אל תסיים ב\"שתפו\" / \"מה דעתכם\" / קריאה לתגובות, אלא אם הסיום שנבחר הוא שאלה.\n\n"

        "## תיקון שמות אחרי תמלול אוטומטי\n"
        "- Chat GBT / צ'אט GPT = ChatGPT; Antropic / אנת'רופיק = Anthropic; Clod / קלוד (מודל) = Claude; "
        "Open AI = OpenAI; Jemini / ג'מיני = Gemini; קורורג / קו-וורק / Co work = Cowork; "
        "גרוק = Grok (של xAI, לא Groq); קודקס = Codex; קרסר = Cursor; Space X = SpaceX; Mid Journey = Midjourney; "
        "גוגל סלייט / גוגל סליידס = Google Slides.\n"
        "- מתקנים רק שיבוש של אותו שם. אסור להחליף שם מוצר או מודל בשם אחר. שם לא מוכר (Fable, Mythos, "
        "Opus 5, Astra וכו') - מעבירים כמו שהוא.\n"
        "- כשהכיתוב של היוצר והתמלול חלוקים על שם - הכיתוב גובר.\n\n"

        "כתוב רק את הפוסט עצמו, מוכן לפרסום. בלי הסברים ובלי ציטוט של ההוראות."
    )

    user_parts = []
    if oembed:
        caption = (oembed.get("title") or "").strip()
        if caption:
            user_parts.append(
                "מטא-דאטה מהיוצר (השתמש רק אם זה מעשיר את הפוסט - אחרת התעלם):\n"
                f"Creator caption: {caption}"
            )
    user_parts.append(f"תמלול הסרטון:\n\n{transcript}")
    user_content = "\n\n".join(user_parts)

    def _call(extra: str = "") -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        if extra:
            messages.append({"role": "user", "content": extra})
        import time
        for attempt in range(3):
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GROQ_MODEL,
                    "messages": messages,
                    "temperature": 0.9,
                    "max_tokens": 2500,
                    "reasoning_effort": "medium",
                },
                timeout=45,
            )
            if resp.status_code != 429 or attempt == 2:
                break
            # Free tier TPM limit (8000/min): wait what Groq asks for, capped.
            m = re.search(r"try again in ([\d.]+)s", resp.text)
            wait = min(float(m.group(1)) + 1 if m else 10, 30)
            log(f"Groq 429; waiting {wait:.0f}s")
            time.sleep(wait)
        if resp.status_code != 200:
            raise ValueError(f"Groq API error {resp.status_code}: {resp.text[:300]}")
        return resp.json()["choices"][0]["message"]["content"].strip()

    post = clean_post(_call(), fmt)
    problems = _violations(post, fmt)
    if problems:
        log(f"Format violations ({', '.join(problems)}); retrying once")
        try:
            retry = clean_post(_call(
                "הפוסט הקודם הפר את הפורמט שנבחר (" + ", ".join(problems) +
                "). כתוב אותו מחדש לפי הפורמט בדיוק."
            ), fmt)
            if retry and len(_violations(retry, fmt)) < len(problems):
                post = retry
        except Exception as e:
            log(f"retry failed ({e}); keeping first version")

    if _repeated_devices(post):
        log(f"Repeated devices survived the retry ({list(_repeated_devices(post))}); dropping repeats")
        post = _drop_repeated_devices(post)

    record_format(fmt)
    return post


def main():
    parser = argparse.ArgumentParser(description="Compose a Hebrew news-post from a TikTok video")
    parser.add_argument("url", help="TikTok video URL")
    parser.add_argument(
        "--raw", action="store_true",
        help="Print raw transcript only (skip news-post composition)"
    )
    # If --ack_id + --chat_id are given, the script pushes the result directly to
    # Telegram (edit the ACK message). Otherwise stdout-only behavior is preserved.
    parser.add_argument("--ack_id", default=None, help="Telegram ACK message id to edit with the result")
    parser.add_argument("--orig_id", default=None, help="Original Telegram message id (reserved)")
    parser.add_argument("--chat_id", default=None, help="Telegram chat id (required with --ack_id to push the result)")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    def push_to_telegram(text: str) -> None:
        """Edit the ACK message with the composed post. No-op if --ack_id/--chat_id missing."""
        if not (args.ack_id and args.chat_id):
            return
        try:
            import re as _re
            import urllib.parse as _up
            import urllib.request as _ur
            with open("D:/Kai/scripts/typing_loop.py", "r", encoding="utf-8") as f:
                m = _re.search(r'BOT_TOKEN\s*=\s*"([^"]+)"', f.read())
            if not m:
                log("push_to_telegram: BOT_TOKEN not found in typing_loop.py")
                return
            token = m.group(1)
            url = f"https://api.telegram.org/bot{token}/editMessageText"
            payload = _up.urlencode({
                "chat_id": args.chat_id,
                "message_id": args.ack_id,
                "text": text,
                "disable_web_page_preview": "true",
            }).encode("utf-8")
            req = _ur.Request(url, data=payload, method="POST")
            with _ur.urlopen(req, timeout=20) as r:
                resp = json.loads(r.read())
                if not resp.get("ok"):
                    log(f"push_to_telegram: telegram error: {resp}")
        except Exception as e:
            log(f"push_to_telegram: error: {e}")

    try:
        transcript, source, canonical = get_transcript(args.url)
        log(f"Transcript source: {source}")

        # Additive: fetch creator metadata for richer summarization. Never fatal.
        oembed = fetch_oembed(canonical)

        if args.raw:
            print(transcript)
            push_to_telegram(transcript)
            return

        if not transcript.strip():
            log("transcript empty; aborting")
            msg = "❌ לא נמצא תמלול לסרטון הזה (ייתכן שהסרטון ללא דיבור)"
            print(msg, file=sys.stderr)
            push_to_telegram(msg)
            sys.exit(1)

        post = summarize_hebrew(transcript, oembed=oembed)
        print(post)
        push_to_telegram(post)

    except ValueError as e:
        msg = f"❌ שגיאה: {e}"
        print(msg, file=sys.stderr)
        push_to_telegram(msg)
        sys.exit(1)
    except Exception as e:
        msg = f"❌ שגיאה בלתי צפויה: {e}"
        print(msg, file=sys.stderr)
        push_to_telegram(msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
