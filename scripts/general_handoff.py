#!/usr/bin/env python
"""Main Kai hands a General-topic request to a domain bot by TAGGING it (multi-bot stage 3, 06/11/2026,
Omer msgs 22581 / 22589, docs/multi_bot_stage3_main_dispatcher.md s.2).

Telegram delivers a bot's message to another bot since Bot API 10.0 (BotFather "Bot-to-Bot Communication
Mode"), so the tag message itself wakes the bot. The bot then puts 👀 on it, works in its own topic and
closes with 👍 + one short line in General (bots/bot_tg.py general-done).

CLI (main Kai's dispatcher only):
  general_handoff.py routes                                   the routing table (bot, @username, topic, domain)
  general_handoff.py tag --bot trading --reply-to <Omer's General msg id> --text "<the request>"
        prints `TAGGED <msg id>` or `DOWN <msg id>` (bot not running: a short notice instead of a tag)
  general_handoff.py history --bot trading [--n 20]           the bot's recent messages (its own bot.db)
"""
import io
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import net_ipv4  # noqa: F401  IPv6 to api.telegram.org is slow on this PC
except Exception:
    pass
import kai_bots  # noqa: E402

RLM = "‏"


def main_token():
    import tg_silent_send
    return tg_silent_send.BOT_TOKEN


def bot_up(cfg):
    """The bot's claude session is alive (cwd-checked) AND its Telegram poller wrote bot.pid."""
    if cfg.get("enabled") is False:
        return False
    if not os.path.exists(os.path.join(cfg.get("state_dir", ""), "bot.pid")):
        return False
    return kai_bots.bot_session_pid(cfg) is not None


def send_general(chat, text, reply_to):
    payload = {"chat_id": chat, "text": text}
    if reply_to:
        payload["reply_parameters"] = {"message_id": int(reply_to), "allow_sending_without_reply": True}
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{main_token()}/sendMessage",
        data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(str(data)[:200])
    mid = int(data["result"]["message_id"])
    try:  # group ids are their own number space; the row is history only (never used to delete)
        import tg_log
        tg_log.save_outgoing(mid, text, int(reply_to) if reply_to else None)
    except Exception:
        pass
    return mid


def handoff(name, text, reply_to, bots=None, up=bot_up, sender=send_general):
    bots = bots if bots is not None else kai_bots.load_config()
    cfg = bots[name]  # KeyError on an unknown bot: the dispatcher must pick from `routes`
    chat = str(cfg["group_id"])
    if not up(cfg):
        msg = f"{RLM}⚠️ {cfg.get('title', name)} לא זמין כרגע, אז לא העברתי לו. תנסה שוב עוד כמה דקות."
        return "down", sender(chat, msg, reply_to)
    return "tagged", sender(chat, f"@{cfg['username']} {text.strip()}", reply_to)


def history(name, n=20, db_path=None):
    """What a bot said / was told lately, from the bot's OWN <home>/bot.db (its hooks save every message).
    Main Kai reads this to answer "מה הבוט עשה?" (Omer msg 22595) - no Telegram delivery, no turn, no loop."""
    sys.path.insert(0, "D:/Kai/bots")
    import bot_tg
    return bot_tg.context(name, n, db_path=db_path)


def route_table(bots=None):
    bots = bots if bots is not None else kai_bots.load_config()
    rows = []
    for name, c in bots.items():
        if c.get("enabled") is False:
            continue
        rows.append(f"{name}\t@{c.get('username')}\ttopic {c.get('topic_name', '')} ({(c.get('topics') or ['?'])[0]})"
                    f"\t{c.get('route', '')}")
    return "\n".join(rows)


def _arg(argv, flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv else default


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "routes":
        print(route_table())
        return 0
    if cmd == "history":
        print(history(_arg(argv, "--bot"), int(_arg(argv, "--n", 20))))
        return 0
    if cmd == "tag":
        st, mid = handoff(_arg(argv, "--bot"), _arg(argv, "--text", ""), _arg(argv, "--reply-to"))
        print(f"{st.upper()} {mid}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.exit(main(sys.argv))
