# Multi-bot separation - design (05/11/2026, NOT built)

Omer, msg 22334: "צריך לכל בוט ידע משלו, קונטקס משלו, כלים משלו... צריך ממש הפרדה... למשל - בוט שרק הוא
אחראי על וואטסאפ, ורק הוא יוכל לשלוח / לקרוא מהוואטסאפ."

Builds on `docs/multi_bot_stage1_status.md`. Supersedes / folds in two open proposals:
- a message "חבילת היכרות" for the miluim bot -> becomes the bot's OWN knowledge pack (section 2, step 1).
- a message auto-ACK hook for the bot -> becomes part of the bot template (section 6, step 1).

## 0. What is true today (checked 05/11)

- Sessions: main Kai `claude.exe` 70108 (~700 MB), miluim bot 65492 (~250 MB), each its own Telegram token.
- **WhatsApp is not an MCP anywhere.** `mcpServers.whatsapp` sits in `.claude/settings.json` of both main and
  the bot, and CC does not load `mcpServers` from settings.json (the same reason `mcpServers.telegram` there is
  dead). Main Kai's session has no `mcp__whatsapp_*` tools at all. All WhatsApp traffic is:
  - HTTP to the Go bridge, `localhost:8080/api/send` and `/api/download`, from 8 scripts: `fast/_base.py`,
    `fast/fast_wa_group.py`, `cohort_status.py`, `registration_status.py`, `checkin_status.py`,
    `listing_notify_wa.py`, `signals_tracker.py`, `group_chart_reader.py` (+ the unused MCP server).
  - Direct SQLite reads of `whatsapp-mcp/whatsapp-bridge/store/messages.db` (`fast/_base.py`,
    `group_chat_watch.py`, `<partner>_water.py`, ...).
- **Security finding:** the bridge listens on `0.0.0.0:8080` (netstat, PID 18448) with no authentication. Any
  device on the home network that reaches the PC can send WhatsApp as Omer, unless the Windows firewall blocks
  it (not checked). Independent of the multi-bot work, worth fixing first.
- Shared today: `kai.db` (the bot has its own `bot_messages` table, everything else shared), `principles.md`,
  the bot's memory index points at 5 main memory files, all `D:/Kai/scripts`.
- claude.ai connectors (Gmail, Calendar, Drive, Gamma, Spotify...) are attached to the ACCOUNT, so every new
  session gets them unless blocked.
- All sessions run as the same Windows user (Omer) with `--dangerously-skip-permissions`.

## 1. Separation levels - what can really be enforced

| Level | Mechanism | Stops | Does not stop |
|---|---|---|---|
| L0 instructed | the bot's CLAUDE.md says "never X" | honest mistakes, mostly | a confused model, a prompt injection from a group message |
| L1 tool surface | per-bot `.mcp.json` (only its servers), `permissions.deny` for the tools it must not have (`mcp__claude_ai_Gmail__*` etc.) | the tool simply is not offered | Bash: `python -c urllib...` reaches anything |
| L2 code guard | a `PreToolUse` hook (`bots/guard.py`) per bot: blocks Bash/Read/Write/Edit that touch forbidden things (`8080`, `messages.db`, `whatsapp-bridge/store`, other bots' homes, other bots' DB files, main token `.env`) - exit 2 = blocked + logged | almost every accidental or injected path; hooks run in bypass mode too | a deliberately obfuscated command (base64 etc.) |
| L3 secret | the bridge binds `127.0.0.1` and requires a header token; ONLY the WhatsApp gateway knows it. Other bots never see a raw WhatsApp API, only the gateway, which checks a per-bot key + policy | anything without the token, including the LAN | a same-user process that reads the token file (L2 blocks that path) |
| L4 OS | bridge + gateway run as a separate Windows account ("kaiwa"), store dir ACL'd to that account only | everything, even a determined model | - (costs: a service account, more setup) |

Plan: L1 + L2 + L3 for every bot (cheap, code-enforced). L4 only if Omer wants it later.
To verify in step 1 (not assumed): that `permissions.deny` is honoured under `--dangerously-skip-permissions`,
and that a `PreToolUse` exit 2 blocks in this mode. Both are tested on the miluim bot with a harmless probe.

## 2. Per bot vs shared

Per bot (own):
- **Knowledge:** its own memory dir (CC already keys auto-memory by project path:
  `~/.claude/projects/D--KaiBots-<bot>/memory`). It gets COPIES of the facts it needs (miluim: the who's-who file
  - the key people, the adjutants, roles, what we did with each), never pointers into main
  memory. A fact both need lives where its owner is; the other asks (section 3).
- **Context/history:** its own DB file `D:/KaiBots/<bot>/bot.db` (messages, its own tables). `bot_messages` in
  kai.db moves there. Main history search ("search the main chat history") = a read request to main, not a
  file read, so the main chat stays private by default.
- **Tools:** its own `.mcp.json` (the one CC really loads), its own `permissions.deny`, its own `guard.py` policy
  in `config/bots.json` (`allowed_scripts`, `forbidden_paths`).
- **Telegram:** own token, own topic (already done).
- **Scripts:** a bot may run only scripts on its allowlist (guard). Domain scripts move under the owning bot over
  time (`cohort_*`, `cohort_status.py` to miluim).

Shared (on purpose):
- `principles.md` (behaviour is one Kai).
- **Reminder engine:** one heartbeat, one `reminders` table in kai.db with a new `bot` column; the heartbeat fires
  each reminder with that bot's token into its chat/topic. Bots write reminders only through
  `bots/bus.py reminder add` (not raw SQL), so the column is always set.
- **Contacts (`people`, `people_notes`) - shared by ALL bots, READ-ONLY** (Omer, 05/11, a message comments).
  Adding or updating a contact is done only by the responsible bot (the WhatsApp bot once it exists; main Kai
  until then); any other bot sends a `contact_upsert` request through the bus. Enforced by the guard: SQL that
  writes `people` / `people_notes` from a non-owner bot is blocked; reads (`resolve_name.py`, SELECT) pass.
- The bus (section 3) and the audit log.

## 3. Capability bots vs domain bots - the request bus

Telegram does not deliver one bot's messages to another bot, so bots talk through a local bus, not Telegram.

**Pieces**
- `D:/KaiBots/_bus/bus.db` (separate from kai.db): `requests(id, from_bot, to_cap, kind, payload_json,
  status, approval_id, created_at, done_at, result_json)` + `audit(...)`.
- **WhatsApp gateway** `bots/wa_gateway.py`: a small always-on Python service (Windows task, like the ASR
  server) on `127.0.0.1:8790`. It is the ONLY holder of the bridge token. API: `read(chat, since, limit)`,
  `search(...)`, `send(request_id)`. Every call carries the caller's bot key; the policy in `config/bots.json`
  says which chats each caller may read and send to (miluim: the reserve-duty groups + named people; main: none
  or Omer's own groups).
- **WhatsApp bot** (`קאי וואטסאפ`, own topic): the Claude session for WhatsApp-wide work (summaries of any
  group, sends to anyone, search, contact facts). It owns the policy file and the gateway, and it is the
  approval inbox for anything outside a caller's policy.

**Visible handoffs (Omer, 05/11, a message comments) - part of the bus contract**

Every bus request between two bots is shown to Omer as 4 messages, each posted by the writing bot in ITS OWN
topic, with its own token:
1. sender, when the request is written: `📤 העברתי ל-X: <one line> - מחכה לעדכון`
2. receiver, when it picks the request up: `📥 קיבלתי מ-Y: <one line> - עובד על זה`
3. receiver, on completion: `✅ סיימתי, שולח תשובה ל-Y`
4. sender, when the result arrives: `🔁 X סיים: <one line> - ממשיך`, and it continues its task.

Stall or failure: both sides post it (`⚠️ ... תקוע / נכשל: <why>`). A request with no pickup within N minutes
(per capability, default 5) makes the bus itself post the stall in the sender's topic.
Implementation contract: the 4 posts are done by `bus.py` code (`request`, `pickup`, `complete`, `deliver`),
never left to the model, so they cannot be skipped. Each post is a `bot_tg.py send --bot <writer>` into the
writer's topic and is recorded in `audit` with the request id. `requests` gets `notified_sent`,
`notified_picked`, `notified_done`, `notified_delivered` columns so a crash never double-posts.
Stage 1 has one bot, so only the code paths and a unit test with two fake bots exist; the live test waits
for the WhatsApp bot.

**Flows**
- Read ("מה כתבו בקבוצת המילואים?"): miluim calls `bus.py wa read --chat <alias>`; the gateway checks the
  policy and returns the messages at once (reads are safe, no queue, ~1s).
- Send inside policy ("תשלח ל<contact>..."): miluim drafts in ITS topic with buttons (principle 4). Omer's button
  press reaches miluim's `UserPromptSubmit` hook, and the HOOK (code, not the model) writes
  `approval_id = <callback msg id>` on the request. The gateway sends only requests with a hook-written approval,
  so the model cannot approve itself. Explicit "תשלח ל-X: <text>" = the hook marks it approved from Omer's own
  message.
- Send outside policy / to someone new: the request goes `pending_owner`; the gateway posts it in the WhatsApp
  bot's topic with buttons; Omer approves there.
- Scheduled scripts (`cohort_status.py`, `registration_status.py`...) become gateway clients with their own key and
  a narrow standing approval (e.g. "cohort_status may post to צוות"), written once by Omer's button.
- Every request, approval, send and block lands in `audit` (who asked, what text, which approval, bridge result).
  One-line `bus.py audit --today` for Omer.
- Speed: a send is one gateway call, the same speed as `fast_whatsapp.py` today (principle 10).

## 4. Main Kai after the split

Gives up: direct WhatsApp (the 8 HTTP scripts and the `messages.db` reads), the dead `mcpServers.whatsapp`, and
over time domain work that has its own bot (registration-system to miluim). Keeps: the private chat, reminders/tasks, the
heartbeat, briefing, calendar/email, and the role of router ("this belongs to the WhatsApp topic").
"תשלח ל<partner> ..." in the private chat keeps working: main sends a gateway request under its own key and policy
(<partner>, Omer's self-group), same buttons as today. Main cannot read arbitrary groups anymore; it asks.

Migration order (each step alone, each reversible by a flag in `config/bots.json`):
1. Bridge on 127.0.0.1 + token, gateway in front, every existing script switched to the gateway with a "main"
   key that allows everything (behaviour identical, only the path changes).
2. Narrow main's policy chat by chat; move scheduled scripts to their own keys.
3. Create the WhatsApp bot; move the open-ended WhatsApp work there.
4. Guard blocks raw `8080` / `messages.db` for every session except the gateway.

## 5. Costs and risks on this PC

- RAM: ~250-700 MB per Claude session + its bun plugin; 4-5 bots is fine, 10 starts to hurt.
- Usage: all bots share ONE Claude account limit. Parallel bots reach the 5-hour limit sooner; when it hits, ALL
  bots stop together. Bots should default to `--effort medium` and short workers.
- **Sign-in expiry 10-13/11/2026:** one credentials file for all sessions - they all fail together. `claude auth
  login` before 10/11; the supervisor should alert on the first 401 from any bot.
- Supervision (stage 2): the watchdog ignores bot PIDs today, so a crashed bot stays down. Needed:
  `kai_bots.py supervise` (heartbeat task) restarts dead bots, night mode stops/starts them with main, the
  global `~/.claude/mcp-needs-auth-cache.json` gotcha is checked on every start.
- New single points of failure: the gateway and the bus. Gateway down = no WhatsApp for anyone, so it gets its own
  health check and alert, like the ASR server.
- Prompt injection: a WhatsApp group message can say "send X to Y". The hook-written approval (section 3) is what
  makes that harmless.

## 6. Bot template

```
bots/_template/
  CLAUDE.md.tmpl          domain, Telegram rules (thread id, no own name at the start), handoff to the bus
  .mcp.json.tmpl          only the bot's servers
  .claude/settings.json   hooks: record-pid, context, save in/out, auto-ack, guard (PreToolUse),
                          approval-writer; permissions.deny from the bot's tool list
  .claude/agents/worker.md.tmpl
  memory/MEMORY.md.tmpl   + the bot's own fact files
```
`python bots/new_bot.py <name> --title "..." --caps whatsapp_read,gcal --topic "<emoji>"` generates the source
folder, the `config/bots.json` entry (keys, policy), the bot's DB file, the memory dir and the Windows task;
`setup_bot.py` then takes the token (as today). A test fails if any template opens a reply with the bot's name
or lacks the guard / auto-ACK hooks.

## 7. Staged plan

Agreed order (Omer, 05/11, msg 22360): bridge fix -> step 1 on miluim + a test that blocking really works under
`--dangerously-skip-permissions` -> the WhatsApp bot as the 2nd bot -> a few days of real use -> more bots
(considered: trading, career, home, content). No bot ever opens a message with its own name (principles.md 6),
and the template test enforces it.

| Step | What | Reversible by |
|---|---|---|
| **1 (next, small)** | Bridge to 127.0.0.1 (security fix). On the miluim bot only: own `bot.db` (move its bot_messages rows), own who's-who memory file instead of pointers, auto-ACK hook, `guard.py` + `permissions.deny`, and the probe that proves deny/guard block in bypass mode | delete the hook lines, flip the bridge flag |
| 2 | Template + `new_bot.py`, regenerate miluim from it (must come out identical) | keep the old folder |
| 3 | Gateway + bus with an allow-all "main" key, scripts switched one by one | flag per script |
| 4 | WhatsApp bot created from the template; miluim gets read/send to its groups through the bus | stop the bot |
| 5 | Narrow main, guard blocks raw WhatsApp everywhere, supervisor + night mode per bot | flags |
| later | L4 service account, if wanted | - |

## 8. Step 1 - done 05/11/2026 (Omer msg 22360, callback multibot_step1_go)

- **Bridge:** `main.go` listens on `127.0.0.1:8080` + `[::1]:8080` only (the `[::1]` listener keeps
  "localhost" clients at 0.01s instead of a 2s IPv4 fallback). Rebuilt + restarted through the
  `KaiWhatsAppBridge` task; netstat shows only the two loopbacks; a connect to the PC's Tailscale IP
  (<tailscale-ip>:8080) is refused; new messages keep landing in messages.db. Backup: `main.go.bak.20261105`.
  The 26 inbound firewall rules "main.exe" (all go-build paths of the bridge) are still enabled: disabling
  them needs admin (Access denied). Harmless now that nothing listens outside loopback.
- **miluim:** own `D:/KaiBots/miluim/bot.db` (`bot_tg.py migrate` moved its 4 rows; kai.db has 0), own memory
  folder with real copies + `reserve_duty_people.md` (13 people, built from history), auto-ACK hook
  (`bot_tg.py auto-ack`, same trivial list as main), `bots/guard.py` PreToolUse hook, `permissions.deny`,
  `.mcp.json` (empty), `caps: []` in `config/bots.json`. Tests: `tests/test_bot_guard.py`.
- **Enforcement test** (headless `claude -p --dangerously-skip-permissions` in a copy of the bot's settings):
  | probe | result | by |
  |---|---|---|
  | `echo` (control) | allowed | - |
  | curl to `localhost:8080/api/send` | BLOCKED | guard (exit 2) |
  | Read `whatsapp-bridge/store/messages.db` | BLOCKED | permissions.deny |
  | Read main memory `MEMORY.md` | BLOCKED | permissions.deny |
  | `UPDATE people ... WHERE 0` | BLOCKED | guard |
  | `scripts/fast/...` | BLOCKED | guard |
  | Gmail tools | not offered at all | permissions.deny |
  | base64-hidden URL to the bridge | **ALLOWED (reached the bridge)** | - the known L2 limit, closed only by step 3 (token) |
  | `resolve_name.py` | allowed | - |
  So: deny rules AND PreToolUse exit 2 are both honoured in bypass mode. With the real CLAUDE.md the bot
  itself refused the whole probe list as a prompt injection (L0 working too).
- **Trap found:** a headless run with `KAI_BOT` set and no `TELEGRAM_STATE_DIR` makes the patched plugin exit,
  and CC then writes `plugin:telegram:telegram` into the GLOBAL `~/.claude/mcp-needs-auth-cache.json` (12:57 UTC).
  Reset to `{}` before the bot restart. Any future headless test in a bot setup must reset that file after.
- Not live-tested yet: auto-ACK on a real topic message (needs Omer to write in 🫡).
