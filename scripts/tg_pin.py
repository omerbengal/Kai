"""
Pin/unpin the live ACK status message in Telegram.

Omer wants the "working on it" status message pinned while a task runs, so he can
keep chatting and still see the current status in the pinned banner without scrolling.

Concurrency: multiple workers can be live at once, but Telegram shows only ONE pinned
banner. We keep a registry of currently-pinned ACK ids in tmp/pinned_acks.json, in the
order they were pinned. Newest wins (the thing that just started is what Omer sees).
When an ACK finishes, we unpin it and re-pin the newest remaining one, so a still-running
task's status returns to the banner.

Both public functions are best-effort and NEVER raise — a pin failure must never break a task.
"""
import json
import os
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net_ipv4  # noqa: F401  IPv6 to Telegram is broken here: +5s per call without this

BOT_TOKEN = os.environ["KAI_TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
REGISTRY = "D:/Kai/tmp/pinned_acks.json"


def _api(method, params):
    """Call a Bot API method. Returns True on ok, False otherwise. Never raises."""
    try:
        data = json.dumps(params).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        body = urllib.request.urlopen(req, timeout=5).read()
        return json.loads(body.decode("utf-8")).get("ok", False)
    except Exception:
        return False


def _read_registry():
    """Return the list of pinned ack ids. Missing/corrupt file => []."""
    try:
        with open(REGISTRY, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [int(x) for x in data if str(x).lstrip("-").isdigit()]
    except Exception:
        pass
    return []


def _write_registry(ids):
    """Atomic write (temp + replace) — parallel workers touch this file."""
    try:
        os.makedirs(os.path.dirname(REGISTRY), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(REGISTRY), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(ids, f)
        os.replace(tmp, REGISTRY)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass


def pin_ack(ack_id, chat_id=CHAT_ID):
    """Register ack_id and pin it (silently). Newest pin wins the banner."""
    try:
        ack_id = int(ack_id)
    except Exception:
        return
    try:
        ids = _read_registry()
        if ack_id in ids:
            ids.remove(ack_id)
        ids.append(ack_id)
        _write_registry(ids)
        _api("pinChatMessage", {
            "chat_id": str(chat_id),
            "message_id": ack_id,
            "disable_notification": True,
        })
    except Exception:
        pass


def unpin_ack(ack_id, chat_id=CHAT_ID):
    """Unregister + unpin ack_id. If other ACKs are still live, re-pin the newest."""
    try:
        ack_id = int(ack_id)
    except Exception:
        return
    try:
        ids = [i for i in _read_registry() if i != ack_id]
        _write_registry(ids)
        _api("unpinChatMessage", {"chat_id": str(chat_id), "message_id": ack_id})
        if ids:
            _api("pinChatMessage", {
                "chat_id": str(chat_id),
                "message_id": ids[-1],
                "disable_notification": True,
            })
    except Exception:
        pass


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] in ("pin", "unpin"):
        cid = sys.argv[3] if len(sys.argv) > 3 else CHAT_ID
        (pin_ack if sys.argv[1] == "pin" else unpin_ack)(sys.argv[2], cid)
        print("ok")
    else:
        print("usage: tg_pin.py pin|unpin <ack_id> [chat_id]")
