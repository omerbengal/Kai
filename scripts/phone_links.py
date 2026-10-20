"""Tap-to-dial phone numbers in Kai's Telegram messages (reminders and tasks).

Why a redirect page: the Bot API accepts <a href="tel:..."> but silently strips it
(verified 20/10/2026: the sent message comes back with no entity), and an inline button
with a tel: URL is rejected. Telegram's own auto-detection only links some +972 formats
and never star codes like *5090. So each number becomes a text_link to a tiny https page
(config/phone_links.json -> dial_base) that forwards the phone to tel:<number>.
The visible text stays exactly what Omer wrote; only the number itself is the link.

If dial_base is not configured, linkify_html() returns None and callers send plain text
exactly as before.

Since 20/10/2026 the Mini App path is OFF ("mini_app": false) and dormant. The live design:
- Israeli mobile / 07x numbers are rewritten to "<phone>": Telegram's server
  auto-links exactly that shape (verified 20/10: <phone> and <phone> get a
  phone_number entity; "+972 54-...", "<phone>", local 054-... and every +972
  landline do not).
- Star codes, landlines and 1-800 numbers (no auto-link) get a contact card (sendContact):
  tapping the number on the card opens the iPhone dialer directly (Omer, 20/10).
  "contact_mode": "card" - a reminder/task alert with one such number IS the card
  (first_name = the alert text); "below" - the text message as usual + a card replying to it.
  Lists and confirmations (fast scripts) always use "below".
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


def _load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# Tested 20/10/2026: the Bot API keeps 128 characters of first_name and 128 of last_name and
# silently cuts longer ones (127 chars + "…"). The card shows "first last" as the name.
CARD_FIRST_MAX = 127   # characters in first_name before the overflow goes to last_name
CARD_NAME_MAX = 250    # whole name (first + space + last); longer texts are shortened


def contact_mode():
    mode = (_load_config().get("contact_mode") or "card").strip()
    return mode if mode in ("card", "below", "off") else "card"


_INTL_RE = re.compile(r"^0(5\d|7\d)(\d{3})(\d{4})$")


def to_intl(raw):
    """Local Israeli mobile/07x -> '<phone>' (Telegram auto-links it); else None."""
    m = _INTL_RE.match(dial_number(raw))
    return f"+972-{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def autolinks(raw):
    """True if Telegram's own detection already turns this number into a phone link."""
    d = dial_number(raw)
    if to_intl(raw):
        return True
    return bool(re.fullmatch(r"\+972(5\d|7\d)\d{7}", d)) and (
        "-" in raw or " " not in raw)


def intl_text(text):
    """Text with every local mobile number rewritten to the auto-linked +972 form."""
    if not text:
        return text
    out, pos = [], 0
    for start, end, raw in find_phones(text):
        out.append(text[pos:start])
        out.append(to_intl(raw) or raw)
        pos = end
    out.append(text[pos:])
    return "".join(out)


def card_numbers(text):
    """Numbers in text that Telegram will NOT link (star codes, landlines, 1-800), de-duplicated."""
    seen, out = set(), []
    for _s, _e, raw in find_phones(text or ""):
        if autolinks(raw):
            continue
        d = dial_number(raw)
        if d not in seen:
            seen.add(d)
            out.append(raw)
    return out


def shorten(text, limit):
    """Deterministic shortening to `limit` characters: cut at a word boundary, add an ellipsis."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut[limit // 2:]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" -,.:;") + "…"


def card_names(text, number=None):
    """(first_name, last_name) for a card that shows `text` as the contact name.

    The number itself is dropped from the name (the card shows it as the phone line) and
    so is a dangling " - " before it. Up to CARD_FIRST_MAX chars go to first_name, the rest
    (still within CARD_NAME_MAX in total) to last_name; longer texts are shortened.
    """
    name = text or ""
    for _s, _e, raw in reversed(find_phones(name)):  # numbers show on the card's phone line
        name = name[:_s] + name[_e:]
    if number:
        name = name.replace(number, "")
    name = re.sub(r"\s*[-:–]\s*$", "", re.sub(r"\s+", " ", name).strip())
    name = re.sub(r"\s+[-:–]\s+(?=[-:–]|$)", "", name).strip() or (number or "")
    name = shorten(name, CARD_NAME_MAX)
    if len(name) <= CARD_FIRST_MAX:
        return name, None
    cut = name[:CARD_FIRST_MAX]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut, name[len(cut):].strip() or None


def label_for(text, number):
    """Short label for a card sent under a message: the line holding the number, minus it."""
    line = next((l for l in (text or "").splitlines() if number in l), text or "")
    line = re.sub(r"^[‏\s•\-*]*", "", line)
    line = re.sub(r"^(?:[^\w\s]{1,3}\s*)+", "", line)  # leading emoji / bullets
    return card_names(line, number)


def contact_payload(chat_id, number, first, last=None, reply_to=None, silent=False):
    p = {"chat_id": chat_id, "phone_number": dial_number(number), "first_name": first or dial_number(number)}
    if last:
        p["last_name"] = last
    if reply_to:
        p["reply_parameters"] = {"message_id": int(reply_to), "allow_sending_without_reply": True}
    if silent:
        p["disable_notification"] = True
    return p


def plan(text, alert=False):
    """How to deliver `text`. Returns dict(mode=..., text=..., cards=[payload kwargs...]).

    mode "card": send ONE sendContact (the alert itself); "text": sendMessage `text`, then
    each entry of `cards` as a reply to it. alert=True only for single reminder/task alerts.
    """
    cfg = _load_config()
    if cfg.get("mini_app"):
        return {"mode": "legacy", "text": text, "cards": []}
    out_text = intl_text(text)
    nums = card_numbers(text)
    mode = contact_mode()
    if mode == "off" or not nums:
        return {"mode": "text", "text": out_text, "cards": []}
    if mode == "card" and alert and len(nums) == 1:
        first, last = card_names(text, nums[0])
        return {"mode": "card", "text": out_text,
                "cards": [{"number": nums[0], "first": first, "last": last}]}
    cards = []
    for n in nums[:3]:
        first, last = label_for(text, n)
        cards.append({"number": n, "first": first, "last": last})
    return {"mode": "text", "text": out_text, "cards": cards}


def _load_base():
    base = (_load_config().get("dial_base") or "").strip()
    return base or None


def dial_number(raw):
    """Digits as a tel: target: keep 0-9 * # and a leading +."""
    raw = raw.strip()
    plus = "+" if raw.startswith("+") else ""
    return plus + re.sub(r"[^0-9*#]", "", raw)


def find_phones(text):
    """List of (start, end, matched_text) for every phone number / star code in text."""
    return [(m.start(), m.end(), m.group(0)) for m in _PHONE_RE.finditer(text or "")]


def mini_app_code(raw):
    """start_param for the Mini App link: alphabet is [A-Za-z0-9_-], so * # + become s h p."""
    return dial_number(raw).replace("*", "s").replace("#", "h").replace("+", "p")


def dial_url(raw, base, bot=None, mini_app=None):
    """Dial page URL. `bot` (config "bot") lets the page jump back to the bot chat after dialing.

    With config "mini_app": true (the bot's Main Mini App points at the dial page in BotFather)
    the link is https://t.me/<bot>?startapp=<code> instead: Telegram opens the page as a Mini
    App, which can really close itself (Telegram.WebApp.close()) after the dialer opens. A plain
    in-app-browser page cannot - on iOS it stays minimized as a bar at the bottom (20/10/2026).
    """
    cfg = None
    if mini_app is None or bot is None:
        cfg = _load_config()
    if mini_app is None:
        mini_app = bool(cfg.get("mini_app"))
    if bot is None:
        bot = (cfg.get("bot") or "").strip()
    if mini_app and bot:
        return f"https://t.me/{bot}?startapp={mini_app_code(raw)}"
    url = base + ("&" if "?" in base else "?") + "n=" + urllib.parse.quote(dial_number(raw), safe="+")
    if bot:
        url += "&b=" + urllib.parse.quote(bot, safe="")
    return url


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
    """sendMessage payload for the dormant Mini App / dial-page path (only when "mini_app": true).

    With mini_app off it returns the original payload: the live path is plan() + contact cards.
    """
    if not linkify or payload.get("parse_mode") or payload.get("entities"):
        return payload
    if not _load_config().get("mini_app"):
        return payload
    linked = linkify_html(payload.get("text", ""))
    if linked is None:
        return payload
    new = dict(payload)
    new["text"] = linked
    new["parse_mode"] = "HTML"
    new.setdefault("link_preview_options", {"is_disabled": True})
    return new
