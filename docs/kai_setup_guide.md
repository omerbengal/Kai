# Kai — Personal AI Assistant Setup Guide

A complete, step-by-step guide for setting up your own Kai instance. Kai is a personal AI assistant powered by **Claude Code** that integrates with **Telegram**, **WhatsApp**, **Google Calendar / Gmail / Drive**, and a local **SQLite** database — and runs autonomously on your machine.

> **Prerequisites:** Windows 10/11 (Kai runs on Mac/Linux too but this guide is Windows-focused), basic comfort with a terminal, ~2 hours.

---

## ⚠️ Read this first — substrate vs. personal

Kai is **two layers**:

| 🟦 **Substrate** (copy as-is) | 🟥 **Personal** (build your own) |
|---|---|
| Claude Code + a Claude model | `CLAUDE.md` (your name, language, rules) |
| Telegram bot + WhatsApp bridge | `memory/` folder (slang, preferences, facts) |
| The DB schema (`tasks`, `reminders`, `people`, `messages`, …) | **Contents** of `people` / `people_notes` |
| The listener → worker → ACK pattern | Your specific contacts and per-contact rules |
| Hooks framework (UserPromptSubmit, PostToolUse) | Your fast-path shortcuts (group sends, custom queries) |
| Heartbeat + morning briefing **engine** | Heartbeat **content**, briefing time/timezone |
| The agent file format | What goes inside each agent — voice/style |

The substrate is **mostly transferable**. The personal layer is **what makes Kai feel like yours** — and it grows organically over the first week or two of talking to it.

**Rule of thumb:** don't try to copy someone else's Kai 1:1. Stand up the substrate, write a 10-line `CLAUDE.md`, send your first Telegram message — and let Kai itself help you build the rest. (See "Building YOUR Kai" at the end.)

Throughout this guide, anything you should customize is marked `<LIKE_THIS>` — placeholder, replace before running.

---

## Architecture in 30 seconds

```
                    ┌──────────────────────┐
   Telegram ──────► │   Claude Code CLI    │ ◄─── Cron / Task Scheduler
                    │  (the main session)  │
   WhatsApp ──────► │                      │ ───► Telegram replies
                    │   • Listener agent   │
   Google APIs ───► │   • Worker agents    │ ───► WhatsApp messages
                    │   • Morning briefing │
                    │   • Heartbeat        │ ───► Calendar/Email actions
                    └──────────────────────┘
                              │
                              ▼
                       ┌──────────────┐
                       │   kai.db     │  ← tasks, reminders, contacts,
                       │  (SQLite)    │    messages, notes, history
                       └──────────────┘
```

The core idea: **Claude Code is the brain**. Everything else (Telegram bot, WhatsApp bridge, Google MCPs, scripts, agents, hooks) is plumbing that lets Claude see your world and act in it.

---

## Step 1 — Install Claude Code

Claude Code is Anthropic's official CLI for Claude. It's the runtime for Kai.

1. **Sign up** at https://claude.ai and get a Claude subscription (Pro or Max) — Kai needs a model that can handle long sessions. Max is recommended for heavy use.
2. **Install Claude Code**:
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```
   (You'll need Node.js 20+ installed first — https://nodejs.org)
3. **First-run login**: open a terminal and run `claude` — it'll open a browser to authenticate.

Test it works: type `hi` at the prompt and you should get a reply.

---

## Step 2 — Create the project structure

Pick a root directory (Omer's is `D:/Kai/`). All paths below are relative to it.

```
Kai/
├── CLAUDE.md                    # Project instructions for Claude (the "system prompt")
├── kai.db                       # SQLite database (created in Step 5)
├── .claude/
│   └── settings.json            # Hooks, permissions, env vars
├── agents/                      # Markdown files describing each agent
│   ├── listener.md
│   ├── morning_briefing.md
│   ├── morning_briefing_launcher.md
│   └── worker.md
├── scripts/                     # Python helpers
│   ├── save_telegram_message.py
│   ├── start_typing.py
│   ├── stop_typing.py
│   ├── transcribe.py
│   ├── heartbeat.py
│   ├── run_morning_briefing.py
│   ├── wake_trigger_server.py
│   └── fast/                    # Single-purpose deterministic scripts
│       ├── _base.py
│       ├── fast_reminder_create.py
│       ├── fast_task_create.py
│       └── fast_whatsapp.py
├── whatsapp-mcp/                # WhatsApp bridge (Step 4)
├── credentials/                 # OAuth tokens (gitignored!)
│   └── google_token.json
└── tmp/                         # Scratch space
```

Create the dirs:
```bash
mkdir -p Kai/{.claude,agents,scripts/fast,credentials,tmp,docs}
cd Kai
git init
```

---

## Step 3 — Set up the Telegram bot

This is Kai's primary chat interface — you'll talk to Kai through Telegram.

1. **Create a bot:** open Telegram, search for `@BotFather`, send `/newbot`. Follow the prompts. Save the **bot token** (looks like `123456:ABC-DEF...`).
2. **Get your chat ID:** send a message to your new bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser — look for `"chat":{"id":NUMBER}`. That `NUMBER` is your chat ID.
3. **Install the Telegram MCP plugin** for Claude Code. Inside Claude Code, run:
   ```
   /telegram:configure
   ```
   It'll prompt you for the bot token and walk you through allowlisting your chat ID.
4. **Test it:** send "hi" to your bot from your phone. Claude Code should receive it as an inbound `<channel>` message.

> ⚠️ **Important:** the bot only sees messages sent **after** the bot was added/started. There's no history API.

---

## Step 4 — Set up the WhatsApp bridge

This is the trickiest piece — WhatsApp has no public API, so we use a self-hosted bridge.

1. **Clone the bridge:**
   ```bash
   git clone https://github.com/lharries/whatsapp-mcp.git
   cd whatsapp-mcp
   ```
2. **Build the Go bridge:**
   ```bash
   cd whatsapp-bridge
   go build -o whatsapp-bridge.exe main.go
   ```
   (Install Go from https://go.dev if you don't have it.)
3. **First-run pairing:** run `./whatsapp-bridge.exe`. It prints a **QR code** in the terminal. On your phone: WhatsApp → Settings → Linked Devices → Link a Device → scan the QR.
4. **Register the MCP server** in Claude Code:
   ```bash
   claude mcp add whatsapp -- python <path-to>/whatsapp-mcp/whatsapp-mcp-server/main.py
   ```
5. **Test:** in Claude Code, ask "list my last 5 WhatsApp chats."

> The bridge stores its session in `whatsapp-bridge/store/whatsapp.db`. **Delete that file** to force re-pairing.
> If WhatsApp logs you out (happens occasionally), repeat step 3.

---

## Step 5 — Create the SQLite database

Kai's memory of tasks, reminders, contacts, and message history lives here.

Create `scripts/init_db.py`:
```python
import sqlite3
conn = sqlite3.connect('kai.db')
conn.executescript("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    priority INTEGER DEFAULT 5,
    domain_id INTEGER,
    due_at DATETIME,
    status TEXT DEFAULT 'open',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    message TEXT NOT NULL,
    trigger_at TEXT NOT NULL,  -- 'YYYY-MM-DD HH:MM:SS' (UTC), space format
    recurrence TEXT,
    status TEXT DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT,
    wa_jid TEXT,
    telegram_username TEXT,
    email TEXT,
    birthday DATE,
    remind_birthday_days INTEGER DEFAULT 3,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS people_notes (
    id INTEGER PRIMARY KEY,
    person_id INTEGER REFERENCES people(id),
    note TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    direction TEXT NOT NULL,   -- 'incoming' | 'outgoing'
    channel TEXT NOT NULL,     -- 'telegram' | 'whatsapp'
    chat_id TEXT,
    msg_id TEXT,
    text TEXT,
    ts DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS action_log (
    id INTEGER PRIMARY KEY,
    agent TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS task_notifications (
    id INTEGER PRIMARY KEY,
    task_id INTEGER REFERENCES tasks(id),
    notified_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
""")
conn.commit()
print("DB initialized.")
```
Run: `python scripts/init_db.py`.

All times stored in **UTC**. Display in local time (Israel = UTC+3 DST).

---

## Step 6 — Write `CLAUDE.md`

This file is loaded into Claude's context on every session — it's Kai's "personality and rules."

Minimum viable version (`CLAUDE.md`):
```markdown
# Kai — Personal AI Assistant

Kai is YOUR_NAME's personal AI assistant. It runs via Claude Code,
talks to YOU on Telegram, and manages tasks, reminders, and messages.

## Database
SQLite at `./kai.db`. Use Python's `sqlite3` module.
All times UTC. Convert to local time (UTC+3) for display.

## Telegram protocol
- Read `agents/listener.md` on every incoming Telegram message.
- ACK first, then act. The user watches Telegram, not the terminal.
- Reply with mcp__plugin_telegram_telegram__reply, never print to stdout.

## WhatsApp
- WhatsApp is the default messaging platform for OTHERS.
- Telegram is ONLY for talking to Kai.

## Language
- Respond in Hebrew if user writes Hebrew, English if English.
- Use light-skin-tone emoji variants (🙏🏻 not 🙏).
```

Customize with your own preferences, contacts, conventions.

---

## Step 7 — Write the listener agent

`agents/listener.md` is the dispatcher invoked on every Telegram message.

Minimum viable version:
```markdown
# Listener — Telegram dispatcher

On every incoming Telegram message:

1. Parse the inbound `<channel>` tag for chat_id and message_id.
2. If voice: ACK with "🎤 מתמלל... ⏳", download via
   `mcp__plugin_telegram_telegram__download_attachment`, transcribe with
   `python scripts/transcribe.py <file>`.
3. Send a generic ACK ("⏳ עובד על זה...") to acknowledge.
4. Spawn a background Agent worker with the user's intent + context.
5. The worker does the actual work and replies to Telegram.
6. Main session stops.

The worker edits the ACK message at each step so the user sees live progress.
Only the final result is a new reply.
```

You'll evolve this over time — Omer's version is ~200 lines with fast-path carve-outs for trivial questions.

---

## Step 8 — Hooks: persist messages, run on startup

`.claude/settings.json`:
```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          {
            "type": "command",
            "command": "python scripts/session_start.py"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python scripts/save_telegram_message.py incoming"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "mcp__plugin_telegram_telegram__reply",
        "hooks": [
          {
            "type": "command",
            "command": "python scripts/save_telegram_message.py outgoing"
          }
        ]
      }
    ]
  }
}
```

The save script reads the hook's JSON from stdin and inserts a row into `messages`. Both directions are persisted so Kai always has history.

---

## Step 9 — Background jobs

### Heartbeat (fires reminders, watchdog)
`scripts/heartbeat.py` runs every minute via **Windows Task Scheduler**:
```python
# Pseudocode
for r in pending_reminders_due_now():
    telegram_send(r.message)
    mark_sent(r.id)
```

Set up the scheduled task:
```powershell
schtasks /create /tn "Kai Heartbeat" /tr "python D:/Kai/scripts/heartbeat.py" /sc minute /mo 1
```

### Morning briefing (09:00 daily)
`scripts/run_morning_briefing.py` composes a Hebrew briefing (weather, calendar, tasks, reminders, birthdays, unread email) and sends via Telegram. Register a cron inside Claude Code:
```
CronCreate cron="0 9 * * *" prompt="agents/morning_briefing_launcher.md" recurring=true durable=true
```

---

## Step 10 — Google APIs (Calendar, Gmail, Drive)

The easiest path is **Claude.ai connectors**: in https://claude.ai → Settings → Connectors, link Google Workspace. Claude Code inherits these automatically.

For a fully local setup:
1. Go to https://console.cloud.google.com → create a project → enable Calendar, Gmail, Drive APIs.
2. Create OAuth credentials (Desktop app).
3. Download the JSON → save as `credentials/google_oauth.json`.
4. Run a one-time auth script to mint `google_token.json`.

---

## Step 11 — Memory system

Claude Code has a built-in memory at `~/.claude/projects/<encoded-path>/memory/`. Write Markdown files there to persist facts across sessions ("user prefers Hebrew", "user's work address is X", "WhatsApp is default messaging platform"). Memory is auto-loaded on session start via an index file `MEMORY.md`. See Omer's `CLAUDE.md` `auto memory` section for the full schema.

---

## Bring-up checklist

- [ ] Claude Code logs in and responds to `hi`.
- [ ] Telegram bot receives & sends messages.
- [ ] WhatsApp bridge paired (QR scan succeeded), can list chats.
- [ ] `kai.db` exists with all tables.
- [ ] `CLAUDE.md` and `agents/listener.md` written.
- [ ] Hooks fire (verify by sending a Telegram message and checking the `messages` table).
- [ ] Heartbeat scheduled task runs every minute.
- [ ] Morning briefing cron registered.
- [ ] Google Calendar / Gmail accessible from Claude Code.
- [ ] Set up your first reminder via Telegram — it fires.

---

## Tips that took Omer a while to learn

1. **Treat the listener as a dispatcher, not an executor.** All real work goes to a background Worker agent. Keeps the main session free for the next inbound message.
2. **Edit the ACK message at every step**, don't send new ones. One live-updating message > 10 "still working" messages.
3. **Always normalize datetime strings to canonical UTC format** `YYYY-MM-DD HH:MM:SS` (space, no T/Z). String comparison in SQLite breaks otherwise.
4. **Build "fast scripts"** for deterministic single operations (create reminder, send WhatsApp, create task). They're 10x cheaper and faster than spawning a worker.
5. **Treat hooks as code** — version-control them, write tests, log liberally.
6. **The user lives on the phone, not in the terminal.** Every action must produce a visible Telegram reply, even silent edits. Otherwise the user thinks Kai is dead.

---

## Where to go from here

Once Kai is alive and you've talked to it for a day, the natural next features:
- **Voice transcription** (Whisper API or Anthropic's native audio).
- **Task domains & priorities** for smarter daily ordering.
- **People profiles** with notes — Kai remembers who's who.
- **TikTok / link summaries** — paste a URL, get a summary back.
- **Calendar integration** for natural-language event creation.
- **Inline keyboards** for confirmations instead of plain-text Y/N.

---

## Building YOUR Kai (the personal layer)

Now the fun part. The substrate is up — empty `kai.db`, minimal `CLAUDE.md`, a listener that just echoes. From here, **don't write your `CLAUDE.md` by hand**. Talk to Kai instead:

1. Send your first messages and **deliberately use your slang, your nicknames, your conventions**. When Kai misunderstands, correct it — and ask it to **save the correction to memory**.
2. Mention a contact: "תזכיר לי לקנות פרחים לאמא". Kai should ask "what's your mom's name / phone?" → answer once → it writes a `people` row. Now it knows.
3. Tell Kai *meta-rules*: "I prefer terse replies", "always use light-skin emojis", "Hebrew unless I write English". These go into `memory/feedback_*.md` files via the auto-memory system.
4. Over the first week, **review your memory folder daily**. Anything you keep correcting → fix in the memory file. Anything Kai got right that surprised you → save a positive-reinforcement memory.
5. Build a fast-script when a single operation repeats 3+ times. Don't generalize early. Concrete > clever.

The substrate makes Kai *functional*. The personal layer makes Kai *yours*. The split is intentional — and the only person who can build the personal layer is you (with Kai's help).

Good luck 🚀

Built by Omer  • inspired by JARVIS, executed in Claude Code.
