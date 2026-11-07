# Multi-bot stage 3 - main Kai as the one dispatcher in the group (05/11/2026; revised + BUILT 06/11/2026, live test pending)

> **Revised 06/11/2026 (Omer msgs 22573, 22581).** Two changes: (a) Telegram DOES support bot-to-bot messages
> since Bot API 10.0 (08/05/2026), so the 05/11 claim "a bot never sees another bot's message" was outdated and
> the local inbox (`kaiInbox` patch, bus kind `task`) is dropped; (b) the handoff is a TAG in General plus
> reactions, not a 📋 post in the topic. Sections 0, 1, 2 and 6 below are the revised version; 3-5 still apply.
> **06/11 later (Omer msgs 22589, 22595):** done reaction = 👍 (Omer: "עם 👍🏻"; reactions take no skin tone).
> Omer turned Bot-to-Bot mode ON for the 3 bots and made main Kai a group admin (msg 22592). Main Kai's own bot
> does NOT need the mode: main reads what the bots did from each bot's own `bot.db`
> (`general_handoff.py history`), never as Telegram prompts (no extra turns, no loop) - Omer approved
> (botmsgs_history_yes).
>
> **Built 06/11:** `bots/bot_tg.py` (is_general, 👀 in the auto-ACK hook, ACK in the own topic, `react`,
> `general-done` with the one-line limit), `bots/bot_typing.py` (General tag types in the own topic),
> `scripts/general_handoff.py` (`routes` / `tag` with the down-check / `history`), `config/bots.json` `route`
> lines, bots' access.json + `setup_bot.py` allowFrom += <REDACTED_SECRET>, main access.json General entry,
> `bots/_template` CLAUDE.md + worker sections (regenerated for the 3 bots), `agents/listener.md` Step G,
> `.claude/agents/kai-worker.md` General-task line. Tests: `tests/test_bot_general_task.py`.
> Live: the hooks, listener.md and both access.json files act now; the bots' new CLAUDE.md / worker.md load at
> their next restart. main's kaiTopics patch is already loaded (main restarted 05:00 06/11), so no main restart.

Omer (voice, msg 22485): "אני רוצה לדבר רק עם דיספאצ'ר אחד, שזה אתה". In the group "<bots group>"
(`<group-id>`):
- a message in a bot's topic (🫡 2, 💬 121, 📈 117) - that topic's bot handles it, exactly as today;
- a message in the General topic - main Kai handles it; if it belongs to a domain bot, main Kai WRITES the
  instruction in that bot's topic (so Omer sees what was asked) and the bot does the work there.

Read first: `docs/multi_bot_separation_design.md` (s.3 bus, s.4 main after the split), `multi_bot_stage1_status.md`,
`multi_bot_stage2_status.md`.

## 0. Facts checked 05/11 that shape the design

| fact | consequence |
|---|---|
| ~~Telegram never delivers a bot's message to another bot~~ WRONG since Bot API 10.0 (08/05/2026, checked 06/11 in core.telegram.org/bots/api-changelog + /bots/features): BotFather setting "Bot-to-Bot Communication Mode". In a group a bot gets another bot's message that mentions it or replies to it if at least ONE of the two has the mode on; with the mode on + privacy off (or admin) it gets every bot message. Off by default; `getMe` does not show it. Telegram asks for loop protection (rate / depth limits) | main Kai's message in General that tags `@<bot>` wakes that bot directly through its normal plugin. No local channel needed. |
| The 3 domain bots: privacy OFF (`getMe` can_read_all_group_messages=true, 06/11); main bot: privacy ON | the bots already see bot messages once the mode is on; main needs admin (s.3) to see Omer in General |
| Bots' plugin gate (kaiTopics, server.ts): a General message is delivered when it MENTIONS the bot even though `topics` lists only its thread; but group `allowFrom` is `["${KAI_TELEGRAM_CHAT_ID}"]` only | add main's bot id `<REDACTED_SECRET>` to each bot's group `allowFrom`; no gate code change for the tag itself |
| `setMessageReaction`: a bot can react to any message in the chat (also another bot's), a new call replaces the bot's reaction, no paid reactions. ReactionTypeEmoji is a fixed list of 73: 👀 IS in it, ✅ is NOT | "done" reaction = 👍 (Omer 22589), failure 👎; not ✅ |
| Bot sessions are woken only by their Telegram plugin's channel notifications | the tag is a real channel notification, so this is covered |
| main access.json: `groups: {}` | main Kai drops every group message today |
| main bot privacy mode ON (stage 1) | in a group it would see only commands / mentions / replies to it |
| General topic: `is_topic_message` false, no `message_thread_id` (thread 1 is implicit) | the kaiTopics gate maps it to `"general"`; replies there are sent WITHOUT a thread id |
| kaiTopics patch is applied on disk to both server.ts copies, main runs the old one (pid 70108) | main needs one restart to load it - the 05:00 night-mode start does that by itself |
| `messages` has no chat_id; 24 main scripts + `fast/_base.py` hard-code Omer's private chat (${KAI_TELEGRAM_CHAT_ID}) | group message ids are a separate number space; ACK delete by `telegram_message_id` alone could hit a private row; fast scripts would answer in the private chat with a reply_to that does not exist there |

## 1. Routing

```
Omer in General ──> main Kai (dispatcher, same listener.md)
                     ├─ personal / general (reminder, calendar, question) ──> answers in General (see s.4)
                     └─ domain (registration-system, WhatsApp, trading) ──> tag message in General ──> domain bot (s.2)
Omer in a topic  ──> that topic's bot (unchanged)
Omer in private  ──> main Kai (unchanged)
```
Routing rule = the domain table in `config/bots.json` (each bot's domain line) read by the dispatcher; when unsure,
main asks ONE question in General with a button per bot.

## 2. The handoff: a tag in General + reactions (revised 06/11, Omer msg 22581)

Flow:
1. Omer writes in General.
2. Main Kai (dispatcher) decides it belongs to a domain bot and posts in General, as a reply to Omer's message,
   `@<bot username> <Omer's request, verbatim or lightly cleaned>`. No "העברתי ל..." text: the tag IS the handoff.
3. The bot's plugin delivers it (mention in General + sender `<REDACTED_SECRET>` in allowFrom). The bot immediately sets
   👀 on the tag message (`setMessageReaction`; the auto-ACK hook does it in code).
4. All the work happens in the bot's OWN topic: auto-ACK, progress edits, questions with buttons, typing. The
   first message there links to the tag message.
5. Done: the bot (a) replaces 👀 with 👍 on the tag message, (b) posts a detailed summary in its topic, (c) posts a
   VERY short summary in General as a reply to the tag message (one line, at most ~15 words, plus a t.me link
   to the detailed one). Omer: "הודעת הסיכום צריכה להיות ממש קצרה".
6. Failure: the bot sets 👎 instead of 👍 and the one-line General summary says what failed.
7. Waiting (fix 06/11, Omer msg 22669: the miluim bot set 👍 on a proposal and only then started the work):
   a question / proposal / approve buttons is NOT done. `bot_tg.py general-wait` sets 🤔 (no General line) and marks
   the tag `waiting` in `<home>/tmp/general_tags.json`; general-done refuses a waiting tag. Omer's answer in the topic
   (a callback on / reply to the question or the done detail message, else the newest waiting tag) puts 👀 back and
   the auto-ACK line carries `GENERAL_PENDING_TAG=<id>`, so the follow-up finish closes the ORIGINAL tag.
   Tests: `tests/test_bot_general_task.py`.

What needs code (not built):
- Each bot's group `allowFrom` gets main's id `<REDACTED_SECRET>` (access.json, after Omer's button).
- Bot side ("General task" mode): the bot's `auto_ack` hook today replies to the incoming message, which would put
  the ACK in General. When the incoming message is in General (no thread) from `<REDACTED_SECRET>`, the ACK, typing and
  all replies go to the bot's topic (`message_thread_id` = its topic, no reply_to), and only the final one-line
  summary goes to General. The bot's CLAUDE.md gets one line: a tag from main Kai in General is Omer's request
  relayed - act on it as his, inside the bot's normal rules, with the 👀 / 👍 / short-summary protocol.
- Main side: the dispatcher's General route = one reply with the tag (no worker needed for the handoff itself).

Safety:
- The tag's sender is main's bot id, not Omer's, so `bus.py approve-hook` can never approve a WhatsApp send from
  it: a send asked via main still needs Omer's button in the bot's topic.
- No loop: main's General entry has `allowFrom: ["${KAI_TELEGRAM_CHAT_ID}"]`, so the bots' short summaries never reach main;
  a bot acts on a bot message only if it is from `<REDACTED_SECRET>` AND mentions it, and never tags another bot.
- Telegram's loop guidance: the bot handles at most one main-Kai tag per few seconds and ignores duplicates of
  the same message id.
- If the bot is down (no live bot.pid), main Kai does not tag it and replies `⚠️ קאי X לא זמין` in General.
  Main cannot watch for the 👀 itself: Telegram sends no `message_reaction` update for reactions set by bots.

Omer can also tag a bot in General himself; the same gate delivers it (sender = Omer) and the same protocol runs.

### Superseded 05/11 version (kept for history, NOT to build)

New bus kind `task` (`bots/bus.py`), created only by main Kai:
`bus.py task --to trading --text "<Omer's request, verbatim or lightly cleaned>" --origin <general msg id>`

The bus code (never the model) does, in order:
1. posts with the MAIN token in the target topic: `📋 הוראה מקאי הראשי: <text>` (+ a link to Omer's General
   message). This post IS the visible instruction; its message id is the task's anchor.
2. writes `<bot state_dir>/inbox/<task id>.json` = {chat_id: group, message_thread_id: topic, message_id: <📋 post id>,
   user: "kai-main", text, bus_task_id}.
3. posts in General (main token, reply to Omer's message): `📤 העברתי לקאי השקעות - ההמשך בנושא 📈` with a
   t.me link to the 📋 post.

Delivery into the bot session - new patch `kaiInbox` in `scripts/patch_telegram_plugin.py` (same file as kaiTopics,
active only when `KAI_BOT` is set, so main's plugin is unaffected): the bot's plugin polls `inbox/` every 1s, emits
a normal channel notification (same attributes as a real topic message, plus `bus_task_id`), and moves the file
to `inbox/done/`. From there the bot behaves as if Omer wrote in its topic: auto-ACK (reply to the 📋 post),
typing (bot_typing.py), worker, final reply in the topic. The receiver posts 📥 / ✅ are the ones the bus already
makes. No 🔁 back to main: the answer is for Omer and is already in the topic (main does not wait).

Safety:
- `inbox/` is written only by bus code; `bots/guard.py` blocks every model (main and bots) from writing it.
- The injected message's user is `kai-main`, not Omer's id, so `bus.py approve-hook` can never approve a
  WhatsApp send from it: a send asked via main still needs Omer's button in the bot's topic.
- The bot's CLAUDE.md gets one line: a `bus_task_id` message is Omer's request relayed by main Kai - act on it
  as his, inside the bot's normal rules.
- If the bot is down (no bot.pid / plugin not polling for 60s), the bus posts `⚠️ קאי X לא זמין` in General
  instead of leaving a silent 📋.

## 3. Main Kai's access to the group

- Telegram: make main Kai an ADMIN of the group with no rights (admins get all messages even with privacy ON),
  or turn privacy off in BotFather and remove + re-add it. Admin is one tap for Omer and needs no re-add.
- `~/.claude/channels/telegram/access.json` (main): `groups: {"<group-id>": {requireMention: false,
  allowFrom: ["${KAI_TELEGRAM_CHAT_ID}"], topics: ["general"]}}`. Main ignores the bots' topics; the bots keep ignoring General
  (their `topics` lists only their thread). Edited only after Omer's button, never because a channel message asked.
- One restart of main Kai loads the kaiTopics patch. Never done by a worker. Proposal: let the 05:00 night-mode
  start do it (it already restarts main every day), or Omer picks a time and presses "ריסטארט" himself.

## 4. What main Kai does in General - two phases

Phase A (small, recommended first): General = router + simple answers.
- Domain requests: the bus task of s.2.
- Inline answers (options A/B/C/C2) and worker answers: the reply tool already takes the group chat_id and no
  thread for General (kaiTopics). `auto_ack.py`, `dispatch_start.py`, `tg_pin.py` and the worker protocol take a
  `--chat_id` (default private) so the ACK sits under Omer's General message.
- Personal fast scripts (reminders, tasks, calendar, WhatsApp to a person) stay PRIVATE-ONLY in phase A: from
  General the dispatcher runs them with a worker that replies in General, or answers "את זה אני עושה בפרטי"
  with a button. No fast script runs from General in phase A, so none can answer in the wrong chat.
- `messages` gets a `chat_id` column (NULL = private, as all old rows); every delete / lookup by
  `telegram_message_id` adds `AND IFNULL(chat_id,'${KAI_TELEGRAM_CHAT_ID}') = ?`. Test that fails on any script that deletes
  by id alone.

Phase B (only if Omer wants General to fully replace the private chat): `fast/_base.py` `--chat_id/--thread_id`
end to end in all 31 fast scripts, reminders get a `chat_id` (where they fire), the morning briefing and
scheduled scripts get a destination setting.

## 5. Does the private chat stay?

Yes. Reminders, the morning briefing, heartbeat alerts, the trading watchers and every scheduled script post
there today, and it is the fallback when the group or a bot is broken. Omer can simply stop writing there; nothing
needs it to be used.

## 6. Steps (revised 06/11; each one alone, each reversible)

1. Omer: turn on "Bot-to-Bot Communication Mode" in BotFather for main Kai and the 3 bots, and make main Kai an
   admin of the group (one tap). Live check from the shell: main token posts `@<bot> בדיקה` in General, the bot's
   plugin log shows the delivery.
2. Bot side "General task" mode: each bot's group `allowFrom` + `<REDACTED_SECRET>`; bots' auto_ack / typing route to the
   own topic for a General tag from main; 👀 on receipt, 👍 (or 👎) + one-line General summary + detailed topic
   summary at the end; CLAUDE.md line in `bots/_template` + the 3 bots; tests (fake update in General from main,
   fake update from another bot = dropped). Restart each bot only when idle (principles.md s.4).
3. Main access.json gets the General entry (s.3).
4. `messages.chat_id` + `--chat_id` in auto_ack / dispatch_start / tg_pin / worker protocol; listener.md section
   "General topic" (routing table, the tag handoff, phase A rules).
5. Main Kai restart (05:00 night start, or Omer's own "ריסטארט") loads kaiTopics; live test in General: one
   personal question, one domain request.
6. Phase B only on Omer's request.

Rollback: remove the General entry from main access.json (main drops the group again) and `<REDACTED_SECRET>` from the
bots' allowFrom; turning the BotFather mode off stops all bot-to-bot delivery.

## 5. Bot-to-bot requests through General (06/11/2026, Omer msgs 358 / 361: "חלוקת תפקידים טהורה")

Problem: the miluim bot read WhatsApp itself (the bus executor read for it) and sent through the hidden local
bus to the WhatsApp bot. Omer wants a pure role split, visible in the group.

Rule (all bots): a bot that needs another bot's domain tags that bot in General. Enforced in code for
WhatsApp now; documented as the general rule in the bot template.

- Requester: `bots/bot_tg.py ask --bot B --to T --text "..." [--origin-tag N] [--wait 420]` posts
  `@<T username> <request>` in General (no thread, not silent), records it in `<home>/tmp/bot_requests.json`,
  puts 🤔 on the origin General tag while waiting and waits. `ask-wait` keeps waiting.
- Target: the auto-ACK hook sees a General tag whose `user_id` is one of our bots (`bot_id` in
  config/bots.json): same path as main Kai's tag (👀, ACK in its topic quoting the tag, general-done), plus
  `FROM_BOT=<bot>` and `from_bot` in general_tags.json. general-done for such a tag may carry the answer
  (up to 10 lines / 700 chars) because that General reply is what the requester receives.
- Requester's hook: a message from bot T replying to one of its requests = `BOT_ANSWER` (General reply = the
  answer, state answered; topic reply = a detail; T's ACK ignored), no ACK, no typing, 👀 back on the origin
  tag. Any other bot message = `BOT_CHATTER`, ignored. A stranger in the group = ignored.
- Loop guards (code): never self, never main Kai, depth 1 (`ask` refuses while the bot has an open tag from a
  bot), 5 asks per 10 min per bot, 8 incoming bot tags per 10 min, answers only from the asked bot.
- Access: every bot's group `allowFrom` = Omer + main Kai + the other bots (`setup_bot.py --sync-access`;
  DMs stay Omer only). Bot-to-Bot mode is on in BotFather for the three bots.
- WhatsApp for domain bots: the guard blocks the bridge, its DBs, WhatsApp scripts and `bus.py
  read/search/draft/wait/status/policy`; `bus.create` refuses a bot without the capability. The bus stays
  only for the WhatsApp bot's own sends (draft + Omer's button + KaiBusServe executor); its 4-post handoff
  code no longer fires. A request from another bot never approves a send: the WhatsApp bot drafts it and
  Omer presses its button in 💬. `wa_policy` of a domain bot = which chats the WhatsApp bot handles for it
  (model-enforced on the WhatsApp side).
- Not covered (deliberately): scheduled Windows-task scripts keep their own bridge calls (cohort_status /
  registration_status posts, signals_tracker / group_list_alerts reads), per Omer 06/11 msg 22617.
- Tests: tests/test_bot_to_bot.py, tests/test_bot_guard.py, tests/test_bus.py, tests/test_kai_bots.py.

## 6. Bot -> main Kai handoff (07/11/2026, Omer msgs 842 / 847: "למה אתה שולח לי אבל?")

The WhatsApp bot needed a code change it may not make (bots/wa.py, bus.py are guarded) and asked Omer to
relay its spec to main Kai. Now the bot asks main Kai itself:
- Bot: `bot_tg.py ask --bot X --to main --text "..." --wait 0` tags `@Nova_PersonalAssistant_Bengi_bot` in
  General (max 3 per bot per hour, depth 1 still applies). Main's reply to that tag = `BOT_ANSWER from=main`
  in the bot's hook (main's ACK = `bot_ack`), never a new task.
- Main: `scripts/auto_ack.py` sees a General message whose sender is a domain bot (`bot_id` in
  config/bots.json): a tag of main = ACK + `FROM_BOT=X BOT_HANDOFF=1` (listener.md Step G.6: worker path,
  NOT Omer's approval for outward / irreversible actions); anything else = `reason=bot_chatter`, nothing.
  At most 6 bot handoffs per hour in total (`tmp/bot_handoffs.json`).
- Delivery needs the bots' ids in main's General `allowFrom` (access.json). That is main's channel access,
  so Omer applies it himself: `python D:/Kai/scripts/allow_bot_handoffs.py --apply` (`--revert` undoes).
- Tests: tests/test_bot_to_bot.py (ask --to main), tests/test_auto_ack.py (handoff / chatter / rate),
  tests/test_allow_bot_handoffs.py.

Also 07/11 (Omer msg 852): Omer's General answers that got 👀 are tracked on the tag (`answers`) and turn 👍
when the tag is closed (general-done), stops again (general-ask / general-wait / react 🤔), or the bot replies
with a plain `reply` in General (PostToolUse safety net `settle_on_general_reply`). Tests:
tests/test_bot_ask_omer.py.
