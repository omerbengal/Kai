"""
Pin/unpin the live ACK status message in Telegram, per chat (07/11/2026: also the bots group).

Omer wants the "working on it" status message pinned while a task runs, so he can
keep chatting and still see the current status in the pinned banner without scrolling.

Per chat (Omer msg 605 in the group, 07/11/2026): the move from one Kai to several left the bots group
"<bots group>" (-1004297864522, a forum) with no pins at all. The registry is now keyed by chat (and by bot
for the domain bots, which pin with their own token through bots/bot_tg.py), so a group ACK and a private
ACK never re-pin each other. In a forum, pinChatMessage on a topic message pins it in that topic.

Concurrency: multiple workers can be live at once, but Telegram shows only ONE pinned banner per chat
(topic). The registry keeps the pinned ACK ids per key in the order they were pinned. Newest wins.
When an ACK finishes, we unpin it and re-pin the newest remaining one of the SAME key.

Registry tmp/pinned_acks.json: {"<key>": [[msg_id, pinned_epoch], ...]}, key = chat_id for the main bot,
"<bot>@<chat_id>" for a domain bot. The old format (a bare list of private ids) is read as the private chat.

Stale pins: a worker that dies before its cleanup leaves its ACK pinned. sweep_stale() (heartbeat, every
minute) unpins anything pinned longer than STALE_SECONDS; clear_main() (session start, main session only)
unpins every main-bot pin, because a restart kills every main worker.

Every public function is best-effort and NEVER raises: a pin failure must never break a task. Unpin of a
message that is not pinned is a harmless no-op.
"""
import json
import os
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net_ipv4  # noqa: F401  IPv6 to Telegram is broken here: +5s per call without this

BOT_TOKEN = os.environ["KAI_TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
GROUP_CHAT_ID = "-1004297864522"
REGISTRY = "D:/Kai/tmp/pinned_acks.json"
STALE_SECONDS = 3 * 3600  # longer than any real task; a pin older than this belongs to a dead worker


def _token(bot):
    if not bot:
        return BOT_TOKEN
    sys.path.insert(0, "D:/Kai/bots")
    import bot_tg  # the domain bot's own token (its .env)
    return bot_tg.token(bot)


def _api(method, params, bot=None):
    """Call a Bot API method. Returns True on ok, False otherwise. Never raises."""
    try:
        data = json.dumps(params).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{_token(bot)}/{method}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        body = urllib.request.urlopen(req, timeout=5).read()
        return json.loads(body.decode("utf-8")).get("ok", False)
    except BaseException:  # bot_tg.token raises SystemExit on a missing .env
        return False


def _key(chat_id, bot=None):
    return f"{bot}@{chat_id}" if bot else str(chat_id)


def _split_key(key):
    """'miluim@-100..' -> ('miluim', '-100..'); '${KAI_TELEGRAM_CHAT_ID}' -> (None, '${KAI_TELEGRAM_CHAT_ID}')."""
    if "@" in key:
        bot, chat = key.split("@", 1)
        return bot, chat
    return None, key


def _read_registry():
    """Return {key: [[id, ts], ...]}. Missing/corrupt file => {}. Old list format => the private chat."""
    try:
        with open(REGISTRY, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if isinstance(data, list):  # pre-07/11/2026: a bare list of private-chat ids
        data = {CHAT_ID: [[x, 0] for x in data]}
    out = {}
    if isinstance(data, dict):
        for key, items in data.items():
            clean = []
            for it in items if isinstance(items, list) else []:
                try:
                    mid, ts = (it[0], it[1]) if isinstance(it, list) else (it, 0)
                    clean.append([int(mid), float(ts)])
                except Exception:
                    pass
            if clean:
                out[str(key)] = clean
    return out


def _write_registry(reg):
    """Atomic write (temp + replace) - parallel workers touch this file."""
    tmp = None
    try:
        os.makedirs(os.path.dirname(REGISTRY), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(REGISTRY), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in reg.items() if v}, f)
        os.replace(tmp, REGISTRY)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass


def pin_ack(ack_id, chat_id=CHAT_ID, bot=None):
    """Register ack_id under its chat (and bot) and pin it silently. Newest pin wins the banner."""
    try:
        ack_id = int(ack_id)
        key = _key(chat_id, bot)
        reg = _read_registry()
        ids = [it for it in reg.get(key, []) if it[0] != ack_id]
        ids.append([ack_id, time.time()])
        reg[key] = ids
        _write_registry(reg)
        _api("pinChatMessage", {
            "chat_id": str(chat_id),
            "message_id": ack_id,
            "disable_notification": True,
        }, bot)
    except Exception:
        pass


def unpin_ack(ack_id, chat_id=CHAT_ID, bot=None):
    """Unregister + unpin ack_id. If other ACKs of the same chat are still live, re-pin the newest."""
    try:
        ack_id = int(ack_id)
        key = _key(chat_id, bot)
        reg = _read_registry()
        before = reg.get(key, [])
        ids = [it for it in before if it[0] != ack_id]
        reg[key] = ids
        _write_registry(reg)
        _api("unpinChatMessage", {"chat_id": str(chat_id), "message_id": ack_id}, bot)
        if ids and len(ids) != len(before):  # only when THIS ack was the banner candidate
            _api("pinChatMessage", {
                "chat_id": str(chat_id),
                "message_id": ids[-1][0],
                "disable_notification": True,
            }, bot)
    except Exception:
        pass


def sweep_stale(max_age=STALE_SECONDS, now=None):
    """Unpin + forget every pin older than max_age, in every chat and bot. Returns how many."""
    n = 0
    try:
        now = now or time.time()
        reg = _read_registry()
        for key, items in list(reg.items()):
            bot, chat = _split_key(key)
            # ts 0 = an entry from the old list format: age unknown, treat it as stale too
            old = [it for it in items if now - it[1] > max_age]
            for mid, _ts in old:
                _api("unpinChatMessage", {"chat_id": chat, "message_id": mid}, bot)
                n += 1
            if old:
                reg[key] = [it for it in items if it not in old]
        if n:
            _write_registry(reg)
    except Exception:
        pass
    return n


def clear_main():
    """Main session start: every main-bot worker died with the old session, so unpin all its pins
    (private chat and group). Domain-bot pins are left alone - those sessions did not restart."""
    n = 0
    try:
        reg = _read_registry()
        for key in [k for k in reg if "@" not in k]:
            for mid, _ts in reg.pop(key):
                _api("unpinChatMessage", {"chat_id": key, "message_id": mid})
                n += 1
        _write_registry(reg)
    except Exception:
        pass
    return n


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) >= 2 and a[0] in ("pin", "unpin"):
        cid = CHAT_ID
        if "--chat_id" in a:
            i = a.index("--chat_id")
            cid = a[i + 1] if i + 1 < len(a) else CHAT_ID
            a = a[:i] + a[i + 2:]
        elif len(a) > 2:  # legacy positional: tg_pin.py pin <id> <chat_id>
            cid = a[2]
        (pin_ack if a[0] == "pin" else unpin_ack)(a[1], cid)
        print("ok")
    elif a[:1] == ["sweep"]:
        print(f"unpinned {sweep_stale()}")
    else:
        print("usage: tg_pin.py pin|unpin <ack_id> [--chat_id ID]   (default: the private chat)\n"
              "       tg_pin.py sweep")
