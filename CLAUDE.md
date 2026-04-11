# Kai — Personal AI Assistant

Kai (קאי) is Omer's personal AI assistant system. It runs autonomously via Claude Code, integrates with Telegram/WhatsApp/Google Calendar/Gmail, and manages tasks, reminders, contacts, and daily briefings.

## Architecture

| Agent | Trigger | Role |
|-------|---------|------|
| `agents/listener.md` | On incoming Telegram message | **Dispatcher** — parses intent, spawns a background worker agent, sends acknowledgment, stops |
| Worker agent (spawned inline) | Spawned by dispatcher | **Executor** — does all actual work, sends Telegram updates at every step |
| `agents/morning_briefing.md` | Daily 09:00 Jerusalem time | Compile & send daily briefing |

> **Note:** `agents/heartbeat.md` exists but is superseded by `scripts/heartbeat.py`, which runs via Windows Task Scheduler. Ignore the agent version.

## Dispatcher Protocol (ALWAYS apply for Telegram messages)

On every incoming Telegram message the main session must:
1. **Read `D:\Kai\agents\listener.md`** — it contains the full dispatcher instructions and the worker prompt template
2. Follow those instructions exactly: transcribe voice if needed, load DB context, send acknowledgment, spawn background worker, stop

The main session **never** executes tasks itself. All execution happens in worker agents.
Multiple messages spawn multiple parallel workers — this is intentional and correct.

## Database (kai.db)

SQLite. Always use Python's `sqlite3` module to query. Key tables:

- **tasks** — title, priority (0–11), domain, due_at (UTC), status (open/done/cancelled)
- **reminders** — message, trigger_at (UTC), recurrence (daily/weekly/monthly/yearly), status
- **people** — contacts with name, birthday, phone, telegram_username, email
- **people_notes** — atomic facts about contacts (preferences, history, etc.)
- **messages** — full Telegram message history (direction: incoming/outgoing)
- **task_notifications** — deduplication log to prevent repeat alerts
- **action_log** — audit trail of agent runs

All times stored in UTC. Israel timezone (Tel Aviv/Jerusalem) = UTC+3 during DST (March–October, currently active) or UTC+2 standard (November–February).

### Example queries
```python
import sqlite3
conn = sqlite3.connect('D:/Kai/kai.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
```

## MCP Servers

| MCP | Tool prefix | Notes |
|-----|-------------|-------|
| Telegram | `mcp__plugin_telegram_telegram__` | Bot API — no history access, only real-time |
| WhatsApp | `mcp__whatsapp_*` | Vendored at `whatsapp-mcp/` |
| Google Calendar | `mcp__claude_ai_Google_Calendar__gcal_*` | Read/create/update |
| Gmail | `mcp__claude_ai_Gmail__*` | Available but needs auth |

### Telegram reply pattern
Always use the `reply` tool (not stdout). Get `chat_id` from inbound `<channel>` tag.

```python
# Never do this — user won't see it
print("response")

# Always do this
mcp__plugin_telegram_telegram__reply(chat_id="${KAI_TELEGRAM_CHAT_ID}", text="...")
```

### Worker update pattern
Workers send a new Telegram message before each major step (not edits). Each update is a real message so Omer sees a live stream of progress. Final result is the last message.

See `agents/listener.md` → Mandatory Telegram Update Protocol for the full standard.

## Message Persistence

All Telegram messages are auto-saved to `messages` table via hooks:
- **UserPromptSubmit** → `scripts/save_telegram_message.py` (incoming)
- **PostToolUse** → same script (outgoing, on `reply` tool calls)

Query history before claiming "I don't have message history."

## Session Context

The dispatcher (listener) loads the last 5 messages from the DB on every message and passes them to the worker in the prompt. Workers do not need to re-query context — they receive it pre-loaded.

See `agents/listener.md` Step 2 for the exact query.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/save_telegram_message.py` | Persist incoming/outgoing Telegram messages to DB |
| `scripts/patch_telegram_plugin.py` | Auto-patch Telegram plugin to expose reply_to metadata |
| `scripts/heartbeat.py` | Standalone heartbeat (for Windows Task Scheduler) |

## Startup

1. `start_claude.bat` launches Claude Code
2. `SessionStart` hook: patches Telegram plugin, sends "קלוד עלה ✅" to Telegram
3. Cron jobs registered each session for heartbeat + morning briefing (IDs saved to `.kai_cron_ids`)

## When to Use Agents vs In-Session

**For Telegram-initiated tasks: always spawn a worker agent. No exceptions.**
Consistency in update behavior matters more than saving 2–3 seconds.

| Situation | Approach |
|-----------|----------|
| Any Telegram message (simple or complex) | Dispatcher spawns background worker |
| Morning briefing (cron-triggered) | In-session (not Telegram-initiated) |
| Heartbeat (Task Scheduler) | `scripts/heartbeat.py` (standalone, not via Agent) |

## Key Conventions

- **Language**: Respond in Hebrew when user writes Hebrew, English when they write English
- **Timezone**: ALWAYS convert UTC times from DB to Tel Aviv local time (UTC+3) before displaying. NEVER show raw UTC times to Omer. Example: DB value `07:44 UTC` → display as `10:44`
- **Priority scale**: 0–4 = low, 5–9 = medium, 10–11 = critical
- **Memory**: Update `C:\Users\Omer\.claude\projects\D--Kai\memory\` when learning new info about Omer or project state
- **DB encoding**: Use `conn.text_factory = str` if Hebrew text appears garbled

## Common Patterns

### Query tasks due today (Jerusalem)
```python
import sqlite3
from datetime import datetime, timezone, timedelta

JERUSALEM = timezone(timedelta(hours=3))  # UTC+3 DST (Mar-Oct), UTC+2 standard (Nov-Feb)
now = datetime.now(JERUSALEM)
today_start = now.replace(hour=0, minute=0, second=0).astimezone(timezone.utc).isoformat()
today_end = now.replace(hour=23, minute=59, second=59).astimezone(timezone.utc).isoformat()

conn = sqlite3.connect('D:/Kai/kai.db')
cur = conn.cursor()
cur.execute("SELECT title, priority, due_at FROM tasks WHERE status='open' AND due_at BETWEEN ? AND ? ORDER BY priority DESC", (today_start, today_end))
```

### Send Telegram with file attachment
```python
mcp__plugin_telegram_telegram__reply(chat_id="${KAI_TELEGRAM_CHAT_ID}", text="...", files=["/abs/path.png"])
```
