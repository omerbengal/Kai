# Multi-bot stage 1 - status / handoff (05/11/2026, ~13:55 local)

Goal (Omer, msgs 22228-22258): Kai split into domain bots in ONE Telegram forum group, a topic per bot.
Stage 1 = only the reserve-duty bot ("קאי מילואים 🪖") in its topic; main Kai unchanged in the private chat.

## Done
- Bot: @<bot-username>, privacy OFF (getMe can_read_all_group_messages=true).
- Token: ONLY in `D:/KaiBots/miluim/telegram_state/.env` (LF line endings - the plugin's JS regex fails on CRLF).
  Omer's a message with the token: redacted in kai.db and deleted from Telegram. It also appears in the old
  worker transcript; Omer may revoke + re-issue it in BotFather once stage 1 works.
- Group "<bots group>" = supergroup `<group-id>` (it migrated from `<group-id>` when Topics was enabled).
  Topic "🫡" = thread `2`. Members: Omer, main Kai (privacy ON; main access.json has `groups: {}` so it drops
  everything from the group), the bot.
- `D:/KaiBots/miluim/telegram_state/access.json`: DM allowlist Omer only; group `<group-id>`
  requireMention=false, allowFrom Omer, topics ["2"].
- `config/bots.json` filled by `bots/setup_bot.py` (username, group_id, topics).
- Topics patch in `scripts/patch_telegram_plugin.py` (TOPICS_REPLACEMENTS, marker `kaiTopics`), APPLIED to both
  server.ts copies (flag `config/telegram_topics_patch.on` exists; watchdog keeps it). Includes the KAI_BOT guard
  (plugin exits if KAI_BOT set without TELEGRAM_STATE_DIR). Backups of the pre-patch files:
  scratchpad of the 05/11 worker (server.ts.bak / server_mkt.ts.bak) - not needed unless rolling back.
  Main Kai loads the patch only on its next restart (optional fields only; no behaviour change in the DM).
- `scripts/kai_watchdog.py` ignores bot claude PIDs (`scripts/kai_bots.py`, `<home>/claude.pid`);
  `restart_claude.ps1` never kills them. Tests: `tests/test_kai_bots.py`, `tests/test_patch_telegram_topics.py`.
- Bot pack: sources `bots/miluim/` (CLAUDE.md, .claude/settings.json hooks, .claude/agents/miluim-worker.md),
  copied to home `D:/KaiBots/miluim` by `bots/start_bot.bat` (robocopy). History table `bot_messages`
  (created on first use by `bots/bot_tg.py`). Memory index seeded at
  `C:/Users/Omer/.claude/projects/D--KaiBots-miluim/memory/MEMORY.md`. Folder trust accepted by Omer.
- Public repo: 6bdee7f (pushed). Later edits (setup_bot LF + token-from-.env, start_bot env clearing, this doc)
  not yet synced; `bots/` and anything with the word "מילואים" is blocked/excluded by the public scanner.

## 05/11 ~13:58 - CONNECTED (3rd launch)
- Launch method now: Windows task `KaiBotMiluim` (on demand, Interactive as Omer, no time limit, IgnoreNew):
  `cmd.exe /k D:\Kai\bots\start_bot.bat miluim`. Start: `schtasks /run /tn KaiBotMiluim` (or
  Start-ScheduledTask). Clean env, like main Kai.
- REAL root cause of "plugin never started" after the 1st launch: `~/.claude/mcp-needs-auth-cache.json`
  held `{"plugin:telegram:telegram": {...}}` from the 11:43 CRLF-token failure. CC then silently SKIPS that
  server in every session (`/reload-plugins` still says "1 plugin MCP server"; no mcp log is even created).
  The key is global, not per project - it would also hit main Kai on its next restart. Fix: rewrite the file
  to `{}` (backup in the 05/11 worker scratchpad). Any future token/plugin start failure: check this file first.
- Result: bot claude 57432, plugin bun 60932 (= D:/KaiBots/miluim/telegram_state/bot.pid), new log
  2026-11-05T11-58-26-494Z.jsonl "Successfully connected" + "Channel notifications registered"; it immediately
  received Omer's queued 11:39 "בדיקה" from thread 2 (bot_messages row 1). Main 70108 / 61976 untouched.

- 05/11 14:13 restart (Omer msg 22324, new "no own name at the start" rule): `taskkill /PID <bot claude> /T /F`
  (after checking its session cwd = D:\KaiBots\miluim) + Stop-ScheduledTask + delete stale bot.pid, then
  Start-ScheduledTask KaiBotMiluim. New: claude 65492, plugin bun 71252, log 2026-11-05T12-13-39-919Z.jsonl
  connected. Cache file stayed `{}`.

- 05/11 15:21 NIGHT MODE covers the bots (Omer msg 22367). Before: kai_night.ps1 Stop-Claude at 02:00 killed
  EVERY claude.exe (the bot's too) and the 05:00 start only ran KaiRestart (main), so the bot died at 02:00 and
  never came back (kai_watchdog ignores bot PIDs; KaiBotMiluim is on-demand, IgnoreNew, and its cmd /k window
  would stay "Running" and block a later /run). Now: `kai_bots.py stop-all` runs first at 02:00 (kills only a
  PID whose ~/.claude/sessions/<pid>.json cwd is the bot home, whole tree, then schtasks /end); at 05:00, 20s
  after main, `kai_bots.py start-all` (drops plugin:telegram:telegram from mcp-needs-auth-cache.json,
  schtasks /end + /run KaiBot<Name>, waits for bot.pid). Both have --dry-run; `kai_night.ps1 -Force -DryRun`
  prints the bot plan. Tests in tests/test_kai_bots.py. Real bot-only stop/start tested 15:21: new claude
  57976, bot.pid 69820, log 2026-11-05T13-21-19-810Z.jsonl connected; main untouched.

- 05/11 ~15:35 main Kai is covered too (Omer msg 22378): `start_claude.bat` runs
  `kai_bots.py clear-auth-cache` right before the claude.exe launch. That bat is the one point shared by
  KaiRestart, restart_claude.ps1 (wt and cmd branches) and the 05:00 night start. The helper removes only
  `plugin:telegram:telegram`, keeps every other key, writes via temp file + os.replace, leaves a missing /
  locked / unreadable file alone, and the CLI always exits 0. Tested: pytest (17) + a dry copy of the bat in
  the worker scratchpad (claude line replaced by echo, cmd-pid line removed); the launch line runs even when
  the python step fails. Main Kai was NOT restarted; the bat change takes effect at its next start.

## Earlier state / open issue (superseded by the section above)
- Two launches so far; the Telegram plugin never polled. Cause 1 (fixed): CRLF .env. Cause 2 (fixed in
  `bots/start_bot.bat`, untested): the launch from inside the main session inherited CLAUDE_CODE_CHILD_SESSION,
  CLAUDE_PID=70108, the messaging socket and session id - the bot ran as a "child session" (transcript off) and
  `/reload-plugins` reported "1 plugin MCP server" but no new telegram MCP log appeared.
- A bot session from the 2nd launch may still be open: claude.exe 50736 (separate "Claude Code" window,
  D:\KaiBots\miluim). Omer was asked to close it (msg after 22309). Do NOT kill claude.exe yourself.
- Main Kai at 13:52: claude 70108, plugin bun 61976 (= main bot.pid), unchanged all day.

## Next steps
1. After Omer closes the window: confirm 50736 gone and 70108 / 61976 alive.
2. Launch with `Start-Process cmd.exe -ArgumentList '/k','D:\Kai\bots\start_bot.bat miluim'` (the bat now
   clears the inherited CLAUDE_* vars). Wait for `D:/KaiBots/miluim/telegram_state/bot.pid` and a new
   `%LOCALAPPDATA%/claude-cli-nodejs/Cache/D--KaiBots-miluim/mcp-logs-plugin-telegram-telegram/*.jsonl`.
   A read-only desktop screenshot (System.Drawing CopyFromScreen) shows the bot window state.
3. If it still does not poll: compare with main's launch (start_claude.bat from Explorer/task, not from a
   Claude child) - consider launching via a scheduled task (`schtasks /run`) for a fully clean environment.
4. Ask Omer for a real message in "🫡"; confirm the reply lands in thread 2 (bot_messages rows, his screenshot).
5. public_sync after it works.

## Stage 2 (not started)
- registration-system alerts / status posts still go through the main Kai (private chat): move them to the miluim bot.
- WhatsApp MCP for the bot: `mcpServers` in a project settings.json is NOT loaded by CC (main's own
  `.claude/settings.json mcpServers.telegram` is known dead) - add the bot's servers via `.mcp.json` in the
  bot home or `claude mcp add --scope project`.
- Autostart: Windows task for each bot (also after night mode 05:00); pre-trust its folder in ~/.claude.json.
- Night mode / session_start_cleanup / heartbeat per bot; reminders column `domain` and which bot sends.
- Main Kai into the group as the General-topic default (topics ["general"]), cross-bot handoff via DB.
- Claude sign-in on this PC expires 10-13/11/2026: `claude auth login` before then (affects main Kai too).
