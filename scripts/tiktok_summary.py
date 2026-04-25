#!/usr/bin/env python3
"""
TikTok video summarizer — uses Supadata API for transcription,
then Groq for Hebrew summary.

Usage:
    python tiktok_summary.py <tiktok_url>
    python tiktok_summary.py <tiktok_url> --raw    # print raw transcript only

Setup:
    Set SUPADATA_API_KEY environment variable (or via setx on Windows):
        setx SUPADATA_API_KEY "your_key_here"
    Sign up free at: https://supadata.ai (100 requests/month free tier)
"""

import sys
import os
import argparse

import requests

# --- Config ---
SUPADATA_API_KEY = os.environ.get("SUPADATA_API_KEY", "")
GROQ_API_KEY = os.environ.get(
    "GROQ_API_KEY",
    ""
)
GROQ_MODEL = "llama-3.3-70b-versatile"


def get_transcript(tiktok_url: str) -> str:
    """Fetch transcript from Supadata API."""
    if not SUPADATA_API_KEY:
        raise ValueError(
            "SUPADATA_API_KEY לא מוגדר. "
            "הירשם בחינם ב-supadata.ai וקבל API key, "
            "ואז הרץ: setx SUPADATA_API_KEY \"your_key_here\""
        )

    resp = requests.get(
        "https://api.supadata.ai/v2/tiktok/transcript",
        params={"url": tiktok_url},
        headers={"x-api-key": SUPADATA_API_KEY},
        timeout=30
    )

    if resp.status_code != 200:
        raise ValueError(
            f"Supadata API error {resp.status_code}: {resp.text[:300]}"
        )

    data = resp.json()

    # Supadata returns: {"transcript": [...], "language": "..."}
    # Each item: {"text": "...", "start": 0.0, "duration": 2.0}
    if "transcript" in data:
        segments = data["transcript"]
        if isinstance(segments, list):
            return " ".join(
                item["text"] for item in segments if item.get("text")
            )
        elif isinstance(segments, str):
            return segments
    if "content" in data:
        return data["content"]
    if "text" in data:
        return data["text"]

    raise ValueError(f"Unexpected Supadata response keys: {list(data.keys())}")


def summarize_hebrew(transcript: str) -> str:
    """Summarize transcript in Hebrew using Groq."""
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY לא מוגדר")

    # Truncate very long transcripts to stay within token limits
    max_chars = 8000
    if len(transcript) > max_chars:
        transcript = transcript[:max_chars] + "... [קוצר]"

    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": GROQ_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "אתה עוזר שמסכם סרטוני TikTok בעברית. "
                        "תן סיכום ברור וקצר (3-5 משפטים) של הסרטון בעברית. "
                        "אם הסרטון מסביר פיצ'ר טכנולוגי, כלול את שם הפיצ'ר, מה הוא עושה, ואיך להשתמש בו. "
                        "אם הסרטון מציג מתכון, כלול את המרכיבים והשלבים העיקריים. "
                        "השתמש בשפה פשוטה וברורה."
                    )
                },
                {
                    "role": "user",
                    "content": f"תמלול הסרטון:\n\n{transcript}"
                }
            ],
            "temperature": 0.3,
            "max_tokens": 500
        },
        timeout=30
    )

    if resp.status_code != 200:
        raise ValueError(
            f"Groq API error {resp.status_code}: {resp.text[:300]}"
        )

    return resp.json()["choices"][0]["message"]["content"].strip()


def main():
    parser = argparse.ArgumentParser(
        description="Summarize a TikTok video in Hebrew"
    )
    parser.add_argument("url", help="TikTok video URL")
    parser.add_argument(
        "--raw", action="store_true",
        help="Print raw transcript only (no summarization)"
    )
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    try:
        transcript = get_transcript(args.url)

        if args.raw:
            print(transcript)
            return

        if not transcript.strip():
            print("❌ לא נמצא תמלול לסרטון הזה (ייתכן שהסרטון ללא דיבור)")
            return

        summary = summarize_hebrew(transcript)
        print(summary)

    except ValueError as e:
        print(f"❌ שגיאה: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ שגיאה בלתי צפויה: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
