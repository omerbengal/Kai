"""TikTok post length bounds (TG 18811, 23/10/2026: a 4-line post was 'too short').

Run: D:/Kai/.venv/Scripts/python.exe -m pytest D:/Kai/tests/test_tiktok_length.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import tiktok_summary as t  # noqa: E402

FMT = {"ending_key": "fact", "emojis": ["🎯"]}

FOUR_LINES = (
    "🎯 עדכון ענק ל-Jeff: רואה תמונות וקורא 400 דפים.\n\n"
    "🏷️ משדרג מטקסט בלבד לתמונות.\n"
    "🏷️ מצא אותי ב-55 פריימים.\n"
    "🏷️ 340 עמודים על אפולו 11: נשארו 45 שניות דלק."
)


def _short_violations(text):
    return [p for p in t._violations(text, FMT) if "קצר" in p]


def test_four_line_post_is_flagged_as_too_short():
    assert _short_violations(FOUR_LINES)


def test_seven_line_post_passes_length():
    post = "\n\n".join(f"שורה מספר {i} עם פרט קונקרטי." for i in range(1, 8))
    assert not _short_violations(post)
    assert not any("more than" in p for p in t._violations(post, FMT))


def test_thirteen_lines_still_flagged_as_too_long():
    post = "\n".join(f"שורה {i}." for i in range(13))
    assert any("more than" in p for p in t._violations(post, FMT))


def test_no_style_asks_for_fewer_lines_than_the_minimum():
    # Every "a-b" range in a style instruction must reach the minimum when the
    # headline line is added; a "3-5 lines" style produced the 4-line post.
    for style in t.FORMAT_STYLES:
        for lo, hi in re.findall(r"(\d+)-(\d+) (?:שורות|נקודות)", style["instruction"]):
            assert int(hi) + 1 >= t.MIN_LINES, (style["key"], lo, hi)
