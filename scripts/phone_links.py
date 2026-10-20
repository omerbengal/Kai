"""Tap-to-dial phone numbers in Kai's Telegram messages (reminders and tasks).

Why a redirect page: the Bot API accepts <a href="tel:..."> but silently strips it
(verified 20/10/2026: the sent message comes back with no entity), and an inline button
with a tel: URL is rejected. Telegram's own auto-detection only links some +972 formats
and never star codes like *5090. So each number becomes a text_link to a tiny https page
(config/phone_links.json -> dial_base) that forwards the phone to tel:<number>.
The visible text stays exactly what Omer wrote; only the number itself is the link.

If dial_base is not configured, linkify_html() returns None and callers send plain text
exactly as before.
"""

import html
import json
import os
import re
import urllib.parse

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "config", "phone_links.json")

# Order matters: longer / more specific forms first.
_PHONE_RE = re.compile(
    r"""
    (?<![\w*+#])                                   # not glued to a word or another number
    (?:
        \*\d{2,6}\#?                               # star codes: *5090, *2700
      | \+972[-\s]?\(?0?\)?\d{1,2}(?:[-\s]?\d){7}  # <phone>, <phone>
      | 1[-\s]?[5789]00[-\s]?\d{2,3}[-\s]?\d{3}    # 1-800-123-456, 1700-50-50-50 style
      | 0(?:5\d|7\d)[-\s]?\d{3}[-\s]?\d{4}         # <phone>, <phone>
      | 0[23489][-\s]?\d{3}[-\s]?\d{4}             # <phone>, <phone>
    )
    (?![\w*#])
    """,
    re.VERBOSE,
)


def _load_base():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            base = (json.load(f).get("dial_base") or "").strip()
            return base or None
    except Exception:
        return None


def dial_number(raw):
    """Digits as a tel: target: keep 0-9 * # and a leading +."""
    raw = raw.strip()
    plus = "+" if raw.startswith("+") else ""
    return plus + re.sub(r"[^0-9*#]", "", raw)


def find_phones(text):
    """List of (start, end, matched_text) for every phone number / star code in text."""
    return [(m.start(), m.end(), m.group(0)) for m in _PHONE_RE.finditer(text or "")]


def dial_url(raw, base):
    return base + ("&" if "?" in base else "?") + "n=" + urllib.parse.quote(dial_number(raw), safe="+")


def linkify_html(text, base=None):
    """HTML (for parse_mode=HTML) with each phone number as a tap-to-dial link.

    Returns None when there is nothing to link or no dial_base is configured, so the
    caller sends the original plain text unchanged.
    """
    base = base or _load_base()
    if not base or not text:
        return None
    phones = find_phones(text)
    if not phones:
        return None
    out, pos = [], 0
    for start, end, raw in phones:
        out.append(html.escape(text[pos:start], quote=False))
        href = html.escape(dial_url(raw, base), quote=True)
        out.append(f'<a href="{href}">{html.escape(raw, quote=False)}</a>')
        pos = end
    out.append(html.escape(text[pos:], quote=False))
    return "".join(out)


def telegram_payload(payload, linkify=True):
    """Return a sendMessage payload with phones linked (adds parse_mode=HTML) or the original."""
    if not linkify or payload.get("parse_mode") or payload.get("entities"):
        return payload
    linked = linkify_html(payload.get("text", ""))
    if linked is None:
        return payload
    new = dict(payload)
    new["text"] = linked
    new["parse_mode"] = "HTML"
    new.setdefault("link_preview_options", {"is_disabled": True})
    return new
