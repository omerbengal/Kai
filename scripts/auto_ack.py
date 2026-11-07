#!/usr/bin/env python
"""
UserPromptSubmit hook: send the dispatcher's transient ACK in CODE, before the model's first turn.

Why (Omer, 22/10/2026, callback ack_hook_build): the "🔄 ⏳" ACK was only an instruction in
agents/listener.md. During a burst of messages the dispatcher skipped it on msg 18252 and Omer
saw nothing. Now the hook sends it the moment the prompt is submitted and tells the model the id.

For every <channel source="plugin:telegram:telegram" ...> block in the prompt (a burst can carry several):
  - reaction (event_type="reaction")              -> no ACK
  - strict trivial allow-list (listener.md Step 0) -> no ACK (dispatcher answers directly)
  - other chat than Omer's DM / the bots group's General topic -> no ACK
  - voice / audio attachment                       -> "🎤 מתמלל... ⏳"
  - everything else (text, photo, document, callback) -> "🔄 ⏳"
General topic (06/11/2026, Omer msg 22689): Omer's messages in the bots group's General topic (group id from
config/bots.json, no message_thread_id) get the same ACK, sent in General (reply to his message, no thread).
Group message ids collide with private ones, so that ACK gets NO `messages` row and its registry key is
prefixed (g_msg_<id>); the output line adds chat_id=<group> so the dispatcher edits/deletes it there.
Callbacks get an ACK: listener.md treats them like messages and every inline callback path
already deletes its ACK (Step shortening rule 3).

Output (stdout, injected into the model's context), one line per channel block:
    AUTO_ACK message_id=<telegram msg id> AUTO_ACK_MSG_ID=<ack id>     (ACK sent / already sent)
    AUTO_ACK message_id=<id> AUTO_ACK_MSG_ID=FAILED                    (dispatcher sends it manually)
    AUTO_ACK message_id=<id> AUTO_ACK_MSG_ID=NONE reason=<trivial|reaction|other_chat>
    AUTO_ACK message_id=<id> AUTO_ACK_MSG_ID=NONE reason=<direct_bot_tag|reply_to_bot> BOT=<name>
        (General: Omer @mentioned a domain bot / replied to its message - the bot owns it, main does nothing)
Nothing at all for prompts without a channel block (cron, system, terminal input).

Idempotent per message: tmp/auto_ack/<key>.ack is created with O_EXCL, so a re-delivered prompt
(or a parallel hook run) reuses the first ACK id instead of sending a second ACK.

Fast and never blocking: one Bot API call per message with a 3s timeout, IPv4 first (net_ipv4),
every error swallowed. Never touches the typing indicator (investigation #043), never pins.

Test without sending:  python D:/Kai/scripts/auto_ack.py --dry-run < sample_prompt.json
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OMER_CHAT_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")


def _general_chat_id():
    try:
        with open("D:/Kai/config/bots.json", encoding="utf-8") as f:
            return str(json.load(f)["group_id"])
    except Exception:
        return "-1004297864522"


GENERAL_CHAT_ID = _general_chat_id()   # the bots group; its General topic = no message_thread_id
TEXT_ACK = "🔄 ⏳"
VOICE_ACK = "🎤 מתמלל... ⏳"
REGISTRY_DIR = "D:/Kai/tmp/auto_ack"
REGISTRY_MAX_AGE_S = 3 * 24 * 3600
SEND_TIMEOUT_S = 3
VOICE_KINDS = {"voice", "audio"}

# Attribute values are quoted and may contain ">" (reply_to_message_text, callback_original_text).
CHANNEL_RE = re.compile(r'<channel((?:\s+[A-Za-z_][\w-]*="[^"]*")*)\s*>([\s\S]*?)</channel>')
ATTR_RE = re.compile(r'([A-Za-z_][\w-]*)="([^"]*)"')

# Mirror of listener.md Step 0 "Trivial = ONLY these exact patterns". Keep the two in sync.
_TRIVIAL_GREETINGS = ["היי", "שלום", "בוקר טוב", "ערב טוב", "לילה טוב", "hi", "hey", "hello"]
_TRIVIAL_ACKS = ["תודה", "אוקיי", "הבנתי", "בסדר", "thanks", "ok", "got it"]
_TRIVIAL_PRESENCE = ["אתה פה", "פה", "ער", "קאי", "שומע", "you there"]
_TRIVIAL_RE = re.compile(
    r"^(?:(?:%s)[!.]*|(?:%s)\??)$" % (
        "|".join(re.escape(w) for w in _TRIVIAL_GREETINGS + _TRIVIAL_ACKS),
        "|".join(re.escape(w) for w in _TRIVIAL_PRESENCE),
    ),
    re.IGNORECASE,
)


def is_trivial(text):
    t = re.sub(r"\s+", " ", (text or "").replace("\u200f", "").replace("\u200e", "")).strip()
    return bool(t) and bool(_TRIVIAL_RE.match(t))


def parse_channels(prompt):
    """[(attrs dict, content)] for every Telegram channel block in the prompt."""
    out = []
    for m in CHANNEL_RE.finditer(prompt or ""):
        attrs = dict(ATTR_RE.findall(m.group(1) or ""))
        # The live plugin tags itself source="plugin:telegram:telegram" (22/10/2026: the old
        # exact "telegram" check skipped every real message, so no ACK was ever sent).
        if "telegram" not in attrs.get("source", "telegram"):
            continue
        out.append((attrs, m.group(2).strip()))
    return out


def is_general(attrs):
    """Omer's message in the bots group's General topic (main Kai is the dispatcher there)."""
    return attrs.get("chat_id") == GENERAL_CHAT_ID and not attrs.get("message_thread_id")


def classify(attrs, content):
    """('none', reason) or ('ack', ack_text)."""
    if attrs.get("event_type") == "reaction" or re.match(r"^\[reaction on message \d+\]", content):
        return ("none", "reaction")
    if attrs.get("chat_id") and attrs.get("chat_id") != OMER_CHAT_ID and not is_general(attrs):
        return ("none", "other_chat")
    if not attrs.get("message_id"):
        return ("none", "no_message_id")
    if attrs.get("attachment_kind") in VOICE_KINDS:
        return ("ack", VOICE_ACK)
    if "callback_data" in attrs or content.startswith("[callback:"):
        return ("ack", TEXT_ACK)
    if not attrs.get("attachment_kind") and not attrs.get("image_path") and is_trivial(content):
        return ("none", "trivial")
    return ("ack", TEXT_ACK)


def _bot_tg():
    if "D:/Kai/bots" not in sys.path:
        sys.path.insert(0, "D:/Kai/bots")
    import bot_tg
    return bot_tg


def bot_reply_owner(attrs):
    """Omer replying in General to a DOMAIN bot's message (its question, nudge, summary line): Telegram
    delivers it to that bot too, so it is that bot's, never main's (07/11/2026, Omer msg 781). Never raises."""
    if not is_general(attrs) or "callback_data" in attrs or not attrs.get("reply_to_message_id"):
        return None
    try:
        return _bot_tg().general_owner(attrs.get("chat_id"), attrs.get("reply_to_message_id"))
    except Exception:
        return None


MAIN_USERNAME = "Nova_PersonalAssistant_Bengi_bot"


def _bot_usernames():
    """{lowercase username: bot name} from config/bots.json (kai_bots), never hard-coded. Never raises."""
    try:
        import kai_bots
        return {str(c["username"]).lstrip("@").lower(): n for n, c in kai_bots.load_config().items()
                if c.get("username")}
    except Exception:
        return {}


def direct_bot_tag(attrs, content, usernames=None):
    """Omer @mentioning a DOMAIN bot in General (07/11/2026, Omer msgs 916 / 921 / 925): Telegram delivers it to
    that bot, which owns it, so main sends no ACK and does nothing. Returns the bot name, else None.
    Not for callbacks, not when main Kai is tagged too. Never raises."""
    if not is_general(attrs) or "callback_data" in attrs:
        return None
    text = (content or "").lower()
    if not text or f"@{MAIN_USERNAME}".lower() in text:
        return None
    names = usernames if usernames is not None else _bot_usernames()
    hits = []
    for uname, name in names.items():
        m = re.search(r"(?<![\w@])@" + re.escape(uname) + r"(?!\w)", text)
        if m:
            hits.append((m.start(), name))
    return min(hits)[1] if hits else None


def bot_owned(attrs, content):
    """('direct_bot_tag' | 'reply_to_bot', bot) when Omer's General message belongs to a domain bot, else None.
    Shared with typing_session_start.py, so no main-Kai typing task opens for it. Never raises."""
    try:
        tag = direct_bot_tag(attrs, content)
        if tag:
            return ("direct_bot_tag", tag)
        owner = bot_reply_owner(attrs)
        if owner:
            return ("reply_to_bot", owner)
    except Exception:
        pass
    return None
BOT_HANDOFF_LOG = "D:/Kai/tmp/bot_handoffs.json"
BOT_HANDOFF_MAX_PER_HOUR = 6   # all domain bots together; beyond it a bot tag is ignored (loop guard)


def bot_sender(attrs):
    """Name of the domain bot that wrote a General message (config/bots.json bot_id), else None. Never raises."""
    uid = attrs.get("user_id")
    if not uid or uid == OMER_CHAT_ID or not is_general(attrs):
        return None
    try:
        return _bot_tg().bot_name_by_id(uid)
    except Exception:
        return None


def bot_handoff_allowed(now=None, path=BOT_HANDOFF_LOG, record=True):
    """Rate limit for bot -> main handoffs. Never raises (a broken log allows)."""
    now = now or time.time()
    try:
        with open(path, encoding="utf-8") as f:
            ts = [t for t in json.load(f) if now - t < 3600]
    except Exception:
        ts = []
    if len(ts) >= BOT_HANDOFF_MAX_PER_HOUR:
        return False
    if record:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(ts + [now], f)
        except Exception:
            pass
    return True


def bot_line(mid, from_bot, content, dry_run=False):
    """A domain bot's message in General (07/11/2026, Omer msgs 842 / 847). Returns (line, handoff):
    a tag of main Kai (`bot_tg.py ask --to main`) = a task (handoff=True, the normal ACK follows);
    anything else (summaries, bot-to-bot tags, its ACKs) = chatter, main does nothing."""
    head = f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID=NONE"
    if f"@{MAIN_USERNAME}".lower() not in (content or "").lower():
        return (f"{head} reason=bot_chatter FROM_BOT={from_bot} (a domain bot's General message that does not"
                " tag main Kai: do NOTHING, no reply)"), False
    if not dry_run and not bot_handoff_allowed():
        return (f"{head} reason=bot_rate_limit FROM_BOT={from_bot} (too many bot handoffs this hour: do"
                " NOTHING)"), False
    return "", True


def open_bot_questions():
    """Lines for every question a domain bot has open for Omer in General (main may route a plain answer)."""
    try:
        qs = _bot_tg().open_questions()
    except Exception:
        return []
    return [f'OPEN_BOT_QUESTION bot={b} question_msg={m} text="{(t or "").replace(chr(34), "")[:90]}"'
            for b, m, t in qs[:5]]


def registry_key(attrs):
    mid = attrs.get("message_id", "")
    if "callback_data" in attrs:
        # A callback carries the id of the bot message with the buttons: two presses share it.
        raw = f"cb_{mid}_{attrs.get('callback_data', '')}_{attrs.get('ts', '')}"
        return re.sub(r"[^\w.-]", "_", raw)[:150]
    key = f"msg_{re.sub(r'[^0-9]', '', mid)}"
    chat = attrs.get("chat_id")
    return key if not chat or chat == OMER_CHAT_ID else "g_" + key   # group ids collide with private


def _prune():
    try:
        now = time.time()
        for name in os.listdir(REGISTRY_DIR):
            p = os.path.join(REGISTRY_DIR, name)
            if now - os.path.getmtime(p) > REGISTRY_MAX_AGE_S:
                os.remove(p)
    except Exception:
        pass


def claim(key):
    """(True, path) if we own this message; (False, existing ack id or None) if already handled."""
    os.makedirs(REGISTRY_DIR, exist_ok=True)
    path = os.path.join(REGISTRY_DIR, key + ".ack")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True, path
    except FileExistsError:
        existing = None
        for _ in range(10):  # a parallel run may still be sending: wait up to ~1.5s for its id
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing = f.read().strip() or None
            except Exception:
                existing = None
            if existing:
                break
            time.sleep(0.15)
        return False, existing


def send_ack(text, reply_to, chat_id=OMER_CHAT_ID):
    import net_ipv4  # noqa: F401  IPv6 to Telegram is broken here
    import tg_silent_send
    import urllib.request
    chat_id = str(chat_id or OMER_CHAT_ID)
    payload = {"chat_id": chat_id, "text": text, "disable_notification": True,
               "reply_parameters": {"message_id": int(reply_to), "allow_sending_without_reply": True}}
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{tg_silent_send.BOT_TOKEN}/sendMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=SEND_TIMEOUT_S) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(str(data)[:200])
    mid = int(data["result"]["message_id"])
    if chat_id != OMER_CHAT_ID:
        return mid  # group: no `messages` row (ids collide with private ones; it has no chat column)
    try:
        tg_silent_send.save(mid, text)  # same `messages` row as every ACK (deletion by id)
    except Exception:
        pass
    return mid


def handle(prompt, dry_run=False):
    lines = []
    for attrs, content in parse_channels(prompt):
        mid = attrs.get("message_id", "?")
        from_bot = bot_sender(attrs)
        handoff_tag = ""
        if from_bot:
            bl, ok = bot_line(mid, from_bot, content, dry_run)
            if not ok:
                lines.append(bl)
                continue
            handoff_tag = (f" FROM_BOT={from_bot} BOT_HANDOFF=1 (a request FROM a domain bot, not from Omer:"
                           " listener.md Step G, bot handoff)")
        tagged = None if from_bot else direct_bot_tag(attrs, content)
        if tagged:
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID=NONE reason=direct_bot_tag BOT={tagged}"
                         f" (Omer tagged {tagged} directly; that bot receives it itself: do NOTHING, no"
                         " reply, no ACK, no tag)")
            continue
        owner = None if from_bot else bot_reply_owner(attrs)
        if owner:
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID=NONE reason=reply_to_bot BOT={owner}"
                         f" (Omer is answering {owner}'s message; that bot receives it itself: do NOTHING, no"
                         " reply, no ACK, no tag)")
            continue
        if is_general(attrs) and not dry_run and not from_bot:
            lines.extend(open_bot_questions())
        kind, val = classify(attrs, content)
        if from_bot and kind == "none" and val == "trivial":
            kind, val = "ack", TEXT_ACK   # a bot's tag is never a greeting
        if kind == "none":
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID=NONE reason={val}")
            continue
        if dry_run:
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID=DRYRUN text={val} key={registry_key(attrs)}"
                         f"{handoff_tag}")
            continue
        try:
            owned, info = claim(registry_key(attrs))
        except Exception:
            owned, info = True, None  # registry broken: still send, a double ACK beats none
        if not owned:
            gen = f" chat_id={attrs.get('chat_id')}" if is_general(attrs) else ""
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID={info or 'FAILED'}{gen} (already sent)")
            continue
        try:
            chat = attrs.get("chat_id") or OMER_CHAT_ID
            ack_id = send_ack(val, mid, chat_id=chat)
            try:
                with open(info, "w", encoding="utf-8") as f:
                    f.write(str(ack_id))
            except Exception:
                pass
            where = f" chat_id={chat}" if chat != OMER_CHAT_ID else ""
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID={ack_id}{where}{handoff_tag}")
        except Exception as e:
            try:
                if info:
                    os.remove(info)  # let a re-delivery try again
            except Exception:
                pass
            lines.append(f"AUTO_ACK message_id={mid} AUTO_ACK_MSG_ID=FAILED ({type(e).__name__})")
    if not dry_run:
        _prune()
    return lines


def main():
    dry_run = "--dry-run" in sys.argv
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        raw = sys.stdin.buffer.read()
        data = json.loads(raw.decode("utf-8", errors="replace"))
        prompt = data.get("prompt", "") or data.get("content", "")
        lines = handle(prompt, dry_run=dry_run)
    except Exception:
        return  # never block the prompt
    if lines:
        print("<system-reminder>\n" + "\n".join(lines) +
              "\n(auto_ack.py hook: use AUTO_ACK_MSG_ID as ACK_MSG_ID and do NOT send another ACK;"
              " NONE = no ACK was sent; FAILED = send the ACK manually as before)\n</system-reminder>")


if __name__ == "__main__":
    main()
