# Multi-bot stage 2 - status / handoff (05/11/2026, from ~22:40 local)

Omer (voice, msg 22465): "למה אנחנו עוד מחכים?" - build the WhatsApp bot and the trading bot now. Design:
`docs/multi_bot_separation_design.md`; stage 1 lessons: `docs/multi_bot_stage1_status.md`.

## Bots

| bot | title | Telegram | topic | caps | task |
|---|---|---|---|---|---|
| miluim | קאי מילואים 🪖 | @<bot-username> | 2 (🫡) | - | KaiBotMiluim |
| whatsapp | קאי וואטסאפ | @<bot-username> | 121 (💬) | whatsapp, contacts_owner | KaiBotWhatsapp |
| trading | קאי השקעות | @<bot-username> | 117 (📈) | - | KaiBotTrading |

Group "<bots group>" = `<group-id>`. Tokens: only in `D:/KaiBots/<bot>/telegram_state/.env` (LF). Omer's msg
22469 that carried both tokens: redacted in kai.db and deleted from Telegram (05/11 ~22:45).

## What was built

- **Generator** `bots/new_bot.py <name> [--regen|--dry-run|--no-task]`: from `bots/_template/` (CLAUDE.md.tmpl,
  worker.md.tmpl) + `bots/_domains/<name>.md` + the config entry, writes `bots/<name>/` (CLAUDE.md, worker,
  settings.json with all hooks + deny list from the caps, empty .mcp.json), the home + state dirs, the memory
  folder with real COPIES (list `MEMORY_FILES` in new_bot.py; only on first run), and the Windows task.
  miluim was regenerated from it (`bots/_domains/miluim.md`); old sources backed up in the 05/11 worker
  scratchpad. Its running session still uses the old files until its next start.
- **setup_bot.py** takes the topic name from `config/bots.json` `topic_name` (topics are named by emoji only:
  "💬", "📈").
- **WhatsApp access** `bots/wa.py`: read / search / chats over the bridge DB (read-only sqlite URI), sender names
  via chats + kai.db contacts + the bridge's whatsmeow_contacts; `send()` exists only for the executor; no send
  CLI. Aliases in `config/bots.json` `wa_aliases` (staff, tickets, ani, ai_updates, potentials, signals_*).
- **Bus** `bots/bus.py` + `D:/KaiBots/_bus/bus.db`: `read` / `search` (create + wait), `draft` (send, needs Omer's
  button `busok:N` / `busno:N`), `wait`, `status`, `policy`, `audit`. The 4 handoff posts (📤 sender, 📥 ✅
  receiver, 🔁 sender) and ⚠️ stall / failure posts are made by the code, each bot with its own token in its own
  topic, at most once (notified_* columns). Own-capability requests (the WhatsApp bot itself) have no posts.
  Policy per bot `wa_policy` {read, send} (aliases / JIDs / "*"), checked at creation AND at execution.
- **Approvals:** `bus.py approve-hook --bot X` is a UserPromptSubmit hook in every bot: only a callback from
  Omer's user id, in that bot's group, for that bot's own draft, approves. The executor refuses any wa_send
  without approval_id. Models cannot run the hook, the executor, wa.send, /api/send, or touch bus.db (guard).
- **Executor** task `KaiBusServe`: `pythonw bots/bus.py serve`, at logon + every 5 min (IgnoreNew), single
  instance by binding 127.0.0.1:8791. Loop errors go to bus.db audit (actor executor, event loop_error).
  Stop: `Disable-ScheduledTask KaiBusServe` + end the pythonw that listens on 8791.
- **Guard** (bots/guard.py): new rules BUS_CODE_ONLY (all bots, incl. the WhatsApp bot: sends only via the bus),
  NO_WA_EXTRA (wa.py for non-WhatsApp bots), identity (`bus.py --from/--bot` must be the bot itself; bus.py
  also checks env KAI_BOT), per-bot `blocked_patterns` from config (trading: live/send modes of the trading
  scripts).
- **Night mode / watchdog:** both new bots are in config/bots.json, so `kai_bots.py stop-all/start-all` and the
  watchdog exclusion cover them. start-all now skips a bot with no token or `enabled: false` (no false FAILED).
- Tests: `tests/test_bus.py` (8), `tests/test_bot_guard.py`, `tests/test_kai_bots.py` - 34 pass.

## Launch log
- 22:50 KaiBotWhatsapp + KaiBotTrading started via schtasks (main 70108 / miluim 57976 untouched). Both stopped
  at the first-run folder trust prompt; Omer asked (msg 22476) to press "Yes, I trust this folder" in both.

- 23:16 both connected (whatsapp claude 63184 / plugin 77552, trading claude 63960 / plugin 21380, logs
  "Successfully connected"); main 70108 / 61976 and miluim 57976 / 69820 untouched; needs-auth cache `{}`.
- 23:17 live handoff test from the shell as miluim: `bus.py read --from miluim --chat staff --limit 5` = request
  1, done in seconds, audit shows post_sent / picked / post_picked / done / post_done / post_delivered.
- public_sync: SYNC_BLOCKED (scanner hits on the multi-bot docs/tests - known, the word list is in them).

## Not done on purpose (needs Omer's OK)
1. Main Kai keeps ALL its WhatsApp scripts (fast/*, cohort_status, signals_tracker...). Migration = design
   section 4 (bridge token + gateway, scripts to the bus with a "main" key, then guard main).
2. Trading scheduled tasks (KaiStockWatch, KaiStocksInPlay, KaiGroupAlerts, KaiSignalsTracker) and their
   Telegram output stay with main Kai (private chat, main token). Moving them = a token/topic switch per script.
3. Reminders from a bot still fire in the private chat (no `bot` column yet).
4. `contact_upsert` requests via the bus are not built; other bots tell Omer to ask in the WhatsApp topic.
5. The base64 / obfuscation limit of the guard remains until the bridge token (L3).
6. miluim runs the OLD instructions until its next start (needs Omer's OK to restart it).

## 05/11 23:28 - typing indicator per bot (Omer msg 22485)
- Bug: bots showed "typing" ~1.5s (the plugin's single sendChatAction), cleared by the auto-ACK message.
- Fix: `bots/bot_typing.py` = port of scripts/typing_lease.py with the bot's own state (`<home>/tmp/typing_*`),
  token and topic (`message_thread_id`). Hooks generated by new_bot.py: open (UserPromptSubmit), pause (reply /
  react, sync), renew --hold 60 (Agent), renew (*), Stop = end-turn that does NOT drop the lease (the bot's
  dispatcher ends its turn right after spawning a background worker). Trace: `<home>/tmp/typing_trace.log`.
- Tests: tests/test_bot_typing.py (8). whatsapp + trading restarted idle at 23:28 (claude 65796 / 75180);
  miluim was mid-deploy (worker active) - NOT restarted, loads the fix at its next idle restart or 02:00 night stop.
- Design for stage 3 (main Kai as dispatcher in General): docs/multi_bot_stage3_main_dispatcher.md.
- 05/11 23:37 parallel tasks (Omer msg 22497): the lease now counts open tasks (incoming msg id + its auto-ACK
  id; `bot_tg.py delete` of the ACK ends the task). Reply #1 pauses only for the reply; closing task 1 resumes
  typing at once while task 2 is open; open tasks keep typing through a long tool call (60s window). No restart
  needed (the hooks call the script by path; settings unchanged). Live check in 📈 with the real loop: typing
  continued after reply #1, stopped 0.2s after reply #2. Tests: test_bot_typing.py 15.
