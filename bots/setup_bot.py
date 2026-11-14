#!/usr/bin/env python
"""One-time setup of a Kai domain bot once Omer sends its token (multi-bot stage 1).

  python D:/Kai/bots/setup_bot.py --bot miluim --token <TOKEN> [--topic-name מילואים]

Does, in order, and stops at the first problem:
  1. getMe: the token works, record the username;
  2. refuses if the token is the main Kai token (would fight its poller);
  3. writes <state_dir>/.env (the plugin reads the token from there);
  4. getUpdates (read only, no offset - updates stay for the plugin): finds the forum
     group and the topic whose name contains --topic-name (or that the bot's test
     message was sent in);
  5. writes <state_dir>/access.json: DM allowlist = Omer only, the group with
     requireMention=false + topics=[that topic], so the bot answers its topic and tags;
  6. updates config/bots.json (username, group_id, topics);
  7. seeds the bot's own auto-memory index (points at the shared facts it needs).
It does NOT launch the session and does NOT touch server.ts.
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, "D:/Kai/scripts")
try:
    import net_ipv4  # noqa: F401
except Exception:
    pass
import kai_bots  # noqa: E402

OMER_ID = os.environ.get("KAI_TELEGRAM_CHAT_ID", "")
MAIN_BOT_ID = "<REDACTED_SECRET>"  # main Kai's bot: its tag in General is a stage-3 handoff (06/11/2026)
MAIN_TOKEN_PREFIX = "<REDACTED_SECRET>:"
MEMORY_ROOT = "C:/Users/Omer/.claude/projects"


def call(token, method):
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def group_allow_from(name, allb):
    """Who may talk to this bot in the group: Omer, main Kai (General handoffs) and every OTHER Kai bot (its
    General tags and its answers to this bot's tags, bot-to-bot since 06/11/2026). DMs stay Omer only."""
    out = [OMER_ID, MAIN_BOT_ID]
    for n, c in allb.items():
        bid = str(c.get("bot_id") or "")
        if n != name and bid and bid not in out:
            out.append(bid)
    return out


def sync_access(name, path, allb):
    """Bring an existing access.json's group allowFrom up to group_allow_from. True = file changed."""
    with open(path, encoding="utf-8") as f:
        access = json.load(f)
    gid = str(allb[name].get("group_id") or "")
    want = group_allow_from(name, allb)
    changed = False
    for g, pol in access.get("groups", {}).items():
        if gid and g != gid:
            continue
        if pol.get("allowFrom") != want:
            pol["allowFrom"] = want
            changed = True
    if changed:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(access, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    return changed


def find_group_and_topic(updates, topic_name):
    """(group_id, thread_id, group_title) from getUpdates results."""
    group_id = title = thread_id = None
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or {}
        mcm = u.get("my_chat_member") or {}
        chat = msg.get("chat") or mcm.get("chat") or {}
        if chat.get("type") == "supergroup":
            if group_id is None or chat.get("is_forum"):
                group_id, title = str(chat["id"]), chat.get("title") or title
        created = msg.get("forum_topic_created") or (msg.get("reply_to_message") or {}).get("forum_topic_created")
        if msg.get("is_topic_message") and msg.get("message_thread_id") is not None:
            name = (created or {}).get("name", "")
            if created is None or topic_name in name:
                if thread_id is None or (created and topic_name in name):
                    thread_id = str(msg["message_thread_id"])
        if created and topic_name in created.get("name", "") and msg.get("message_thread_id"):
            thread_id = str(msg["message_thread_id"])
    return group_id, thread_id, title


def seed_memory(home):
    slug = home.replace(":", "-").replace("/", "-").replace("\\", "-")
    mem = os.path.join(MEMORY_ROOT, slug, "memory")
    os.makedirs(mem, exist_ok=True)
    idx = os.path.join(mem, "MEMORY.md")
    if os.path.exists(idx):
        return idx
    shared = "C:/Users/Omer/.claude/projects/D--Kai/memory"
    with open(idx, "w", encoding="utf-8") as f:
        f.write(
            "# Memory Index (קאי מילואים)\n\n"
            "Behaviour: read `D:/Kai/agents/principles.md` first.\n"
            "Shared facts live in the main Kai memory; read these by absolute path when relevant:\n\n"
            f"- [Registration-system clones]({shared}/project_registration_systems.md)\n"
            f"- [WA group members need screenshots]({shared}/reference_wa_group_members_screenshots.md)\n"
            f"- [Names verbatim / Omer]({shared}/user_name_spelling.md)\n\n"
            "New facts learned in this bot's domain go here (this folder).\n")
    return idx


def main(argv):
    if "--sync-access" in argv:  # every bot: add the other bots to its group allowFrom (bot-to-bot)
        allb = kai_bots.load_config()
        for n, c in allb.items():
            p = os.path.join(c["state_dir"], "access.json")
            if os.path.exists(p):
                print(n, "changed" if sync_access(n, p, allb) else "unchanged")
        return 0
    name = argv[argv.index("--bot") + 1]
    cfg = kai_bots.bot(name)
    topic_name = argv[argv.index("--topic-name") + 1] if "--topic-name" in argv else cfg.get("topic_name", "מילואים")
    if "--token" in argv:
        tok = argv[argv.index("--token") + 1].strip()
    else:  # preferred: token already stored in the state dir, never on a command line
        with open(os.path.join(cfg["state_dir"], ".env"), encoding="utf-8") as f:
            tok = f.read().split("TELEGRAM_BOT_TOKEN=", 1)[1].strip()

    if tok.startswith(MAIN_TOKEN_PREFIX):
        raise SystemExit("this is the MAIN Kai token - refusing")
    me = call(tok, "getMe")
    if not me.get("ok"):
        raise SystemExit(f"getMe failed: {me}")
    username = me["result"]["username"]
    can_read_all = me["result"].get("can_read_all_group_messages")
    print(f"bot @{username} ok; privacy off = {can_read_all}")

    state = cfg["state_dir"]
    os.makedirs(state, exist_ok=True)
    # LF only: the plugin parses .env with a JS regex whose "." stops at "\r" (CRLF = no token)
    with open(os.path.join(state, ".env"), "w", encoding="utf-8", newline="\n") as f:
        f.write(f"TELEGRAM_BOT_TOKEN={tok}\n")

    ups = call(tok, "getUpdates?timeout=0&allowed_updates=" +
               urllib.request.quote(json.dumps(["message", "my_chat_member"])))
    group_id, thread_id, title = find_group_and_topic(ups.get("result", []), topic_name)
    # 14/11/2026: main Kai can create the topic itself (createForumTopic) and record it in config first; a new
    # bot never saw that topic's creation in its own getUpdates, so the config values are used when present.
    if cfg.get("group_id") and cfg.get("topics"):  # config first: discovery may pick any topic message
        group_id, thread_id = str(cfg["group_id"]), str(cfg["topics"][0])
    print(f"group={group_id} ({title}) topic={thread_id}")
    if not group_id or not thread_id:
        raise SystemExit("group or topic not found yet - write one message in the topic and run again")

    access = {
        "dmPolicy": "allowlist",
        "allowFrom": [OMER_ID],
        "groups": {group_id: {"requireMention": False,
                              "allowFrom": group_allow_from(name, kai_bots.load_config()),  # + main, bots
                              "topics": [thread_id]}},
        "pending": {},
        "ackReaction": "",
    }
    with open(os.path.join(state, "access.json"), "w", encoding="utf-8") as f:
        json.dump(access, f, ensure_ascii=False, indent=2)

    with open(kai_bots.CONFIG, encoding="utf-8") as f:
        full = json.load(f)
    full["bots"][name].update({"username": username, "bot_id": str(me["result"]["id"]), "group_id": group_id,
                               "topics": [thread_id]})
    with open(kai_bots.CONFIG, "w", encoding="utf-8") as f:
        json.dump(full, f, ensure_ascii=False, indent=2)

    print("memory index:", seed_memory(cfg["home"]))
    print("SETUP_OK" + ("" if can_read_all else " WARN_PRIVACY_ON"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
