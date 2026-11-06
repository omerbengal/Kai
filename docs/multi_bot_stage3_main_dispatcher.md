# Multi-bot stage 3 - main Kai as the one dispatcher in the group (05/11/2026, DESIGN ONLY, not built)

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
| Telegram never delivers a bot's message to another bot (also with privacy off) | a visible post by main Kai in a topic can NOT wake the bot. A real local channel is needed. |
| Bot sessions are woken only by their Telegram plugin's channel notifications | the bus alone (a DB row) cannot start a turn in a bot's Claude session |
| main access.json: `groups: {}` | main Kai drops every group message today |
| main bot privacy mode ON (stage 1) | in a group it would see only commands / mentions / replies to it |
| General topic: `is_topic_message` false, no `message_thread_id` (thread 1 is implicit) | the kaiTopics gate maps it to `"general"`; replies there are sent WITHOUT a thread id |
| kaiTopics patch is applied on disk to both server.ts copies, main runs the old one (pid 70108) | main needs one restart to load it - the 05:00 night-mode start does that by itself |
| `messages` has no chat_id; 24 main scripts + `fast/_base.py` hard-code Omer's private chat (${KAI_TELEGRAM_CHAT_ID}) | group message ids are a separate number space; ACK delete by `telegram_message_id` alone could hit a private row; fast scripts would answer in the private chat with a reply_to that does not exist there |

## 1. Routing

```
Omer in General ──> main Kai (dispatcher, same listener.md)
                     ├─ personal / general (reminder, calendar, question) ──> answers in General (see s.4)
                     └─ domain (registration-system, WhatsApp, trading) ──> bus task ──> 📋 post in the topic + inbox ──> domain bot
Omer in a topic  ──> that topic's bot (unchanged)
Omer in private  ──> main Kai (unchanged)
```
Routing rule = the domain table in `config/bots.json` (each bot's domain line) read by the dispatcher; when unsure,
main asks ONE question in General with a button per bot.

## 2. The handoff: visible post + real request (one code path)

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

## 6. Steps (each one alone, each reversible)

1. Bus kind `task` + 📋/📤 posts + `inbox/` files, guard rule for `inbox/`, tests (two fake bots). No live effect.
2. Patch `kaiInbox` in the plugin (KAI_BOT only), tests like `test_patch_telegram_topics.py`; restart each
   bot only when idle (principles.md s.4) to load it; live test: `bus.py task --to trading` from the shell.
3. Omer: make main Kai an admin in the group (one tap). Then main access.json gets the General entry.
4. `messages.chat_id` + `--chat_id` in auto_ack / dispatch_start / tg_pin / worker protocol; listener.md section
   "General topic" (routing table, phase A rules).
5. Main Kai restart (05:00 night start, or Omer's own "ריסטארט") loads kaiTopics; live test in General: one
   personal question, one domain request.
6. Phase B only on Omer's request.

Rollback: remove the General entry from main access.json (main drops the group again); bus `task` and the
inbox stay harmless when unused.
