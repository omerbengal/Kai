# Nova Personal Assistant — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Nova, a 24/7 personal AI assistant for Omer — three Claude Code agents (Listener, Heartbeat, Morning Briefing) sharing a SQLite database, orchestrated via Telegram.

**Architecture:** A Listener Agent responds to Omer's Telegram messages in real time (text + voice). A Heartbeat Agent runs every 60 seconds to fire due reminders and task nudges. A Morning Briefing Agent runs at 09:00 Jerusalem time daily to send a structured digest. All agents share `D:\Nova\nova.db` as the source of truth.

**Tech Stack:** SQLite (via `@modelcontextprotocol/server-sqlite`), Claude Code CronCreate (durable), Telegram Bot MCP, WhatsApp MCP, Google Calendar MCP (already configured), Gmail MCP (already configured), Brave Search MCP, OpenAI Whisper API.

**Spec:** `D:\Nova\docs\superpowers\specs\2026-04-10-nova-personal-assistant-design.md`

---

## Task 1: Database — schema.sql, seed.sql, and nova.db

**Files:**
- Create: `D:\Nova\schema.sql`
- Create: `D:\Nova\seed.sql`
- Creates at runtime: `D:\Nova\nova.db`

- [ ] **Step 1: Write schema.sql**

Create `D:\Nova\schema.sql` with this exact content:

```sql
-- Lookup tables
CREATE TABLE IF NOT EXISTS relationship_types (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS domains (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS note_categories (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

-- People
CREATE TABLE IF NOT EXISTS people (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT NOT NULL,
    relationship_id      INTEGER REFERENCES relationship_types(id),
    birthday             DATE,
    remind_birthday_days INTEGER DEFAULT 3,
    phone                TEXT,
    telegram_username    TEXT,
    email                TEXT,
    created_at           DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS people_nicknames (
    id        INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    nickname  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS people_notes (
    id          INTEGER PRIMARY KEY,
    person_id   INTEGER NOT NULL REFERENCES people(id),
    category_id INTEGER NOT NULL REFERENCES note_categories(id),
    note        TEXT NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Reminders
CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY,
    message    TEXT NOT NULL,
    trigger_at DATETIME NOT NULL,
    recurrence TEXT    CHECK (recurrence IN ('daily','weekly','monthly','yearly')),
    status     TEXT    DEFAULT 'pending' CHECK (status IN ('pending','fired','dismissed')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Tasks
CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    notes      TEXT,
    priority   INTEGER DEFAULT 5 CHECK (priority BETWEEN 0 AND 11),
    domain_id  INTEGER REFERENCES domains(id),
    due_at     DATETIME,
    status     TEXT DEFAULT 'open' CHECK (status IN ('open','done','cancelled')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Task notification deduplication log
CREATE TABLE IF NOT EXISTS task_notifications (
    id                INTEGER PRIMARY KEY,
    task_id           INTEGER NOT NULL REFERENCES tasks(id),
    notification_type TEXT NOT NULL,
    -- Priority 10-11 morning briefing types: '7d','6d','5d','4d','3d','2d','1d','overdue_daily'
    -- Priority 10-11 heartbeat types:        '12h','6h','3h','1.5h'
    -- Priority 5-9  heartbeat types:          '24h','12h','6h','3h','1.5h'
    -- All priorities heartbeat:               'overdue'
    sent_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(task_id, notification_type)
);

-- Agent action log
CREATE TABLE IF NOT EXISTS action_log (
    id         INTEGER PRIMARY KEY,
    agent      TEXT NOT NULL CHECK (agent IN ('listener','heartbeat','morning_briefing')),
    action     TEXT NOT NULL,
    details    TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

- [ ] **Step 2: Write seed.sql**

Create `D:\Nova\seed.sql`:

```sql
INSERT OR IGNORE INTO relationship_types (name) VALUES
    ('family'),
    ('friend'),
    ('work'),
    ('acquaintance'),
    ('other');

INSERT OR IGNORE INTO domains (name) VALUES
    ('work'),
    ('hobbies'),
    ('family'),
    ('friends'),
    ('health'),
    ('finance'),
    ('personal');

INSERT OR IGNORE INTO note_categories (name) VALUES
    ('work'),
    ('preferences'),
    ('history'),
    ('family'),
    ('health'),
    ('personality'),
    ('goals'),
    ('other');
```

- [ ] **Step 3: Initialize nova.db**

```bash
cd D:/Nova
sqlite3 nova.db < schema.sql
sqlite3 nova.db < seed.sql
```

Expected: no output, no errors.

- [ ] **Step 4: Verify schema and seed**

```bash
sqlite3 nova.db "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
```

Expected output (exactly these 10 tables):
```
action_log
domains
note_categories
people
people_nicknames
people_notes
relationship_types
reminders
task_notifications
tasks
```

```bash
sqlite3 nova.db "SELECT name FROM relationship_types; SELECT name FROM domains; SELECT name FROM note_categories;"
```

Expected: all seeded values listed.

- [ ] **Step 5: Insert test row and verify FK enforcement**

```bash
sqlite3 nova.db "
PRAGMA foreign_keys = ON;
INSERT INTO people (name, relationship_id) VALUES ('Test Person', 1);
SELECT id, name FROM people;
DELETE FROM people WHERE name = 'Test Person';
"
```

Expected: row inserted, then deleted cleanly.

- [ ] **Step 6: Commit**

```bash
cd D:/Nova
git add schema.sql seed.sql
git commit -m "feat: add SQLite schema and seed data for Nova"
```

---

## Task 2: MCP Server Installation & Configuration

**Files:**
- Create: `D:\Nova\.claude\settings.json`

**Note:** Google Calendar and Gmail MCPs are already configured via the Claude.ai integration. Steps below cover the remaining MCPs.

- [ ] **Step 1: Install SQLite MCP**

```bash
npm install -g @modelcontextprotocol/server-sqlite
```

Verify:
```bash
npx @modelcontextprotocol/server-sqlite --version
```
Expected: version string printed.

- [ ] **Step 2: Install Brave Search MCP**

```bash
npm install -g @modelcontextprotocol/server-brave-search
```

Get a free API key at https://api.search.brave.com — copy the key, you'll need it in Step 5.

- [ ] **Step 3: Set up Telegram Bot**

1. Open Telegram and message `@BotFather`
2. Send `/newbot` — follow prompts, name it "Nova"
3. Copy the bot token (format: `123456789:ABCdef...`)
4. Install the Telegram MCP:

```bash
pip install mcp-telegram
```

Or if using the Node-based version:
```bash
npm install -g @modelcontextprotocol/server-telegram
```

- [ ] **Step 4: Set up WhatsApp MCP**

```bash
git clone https://github.com/lharries/whatsapp-mcp.git D:/Nova/whatsapp-mcp
cd D:/Nova/whatsapp-mcp
pip install -r requirements.txt
```

On first run, scan the QR code with your WhatsApp mobile app to authenticate:
```bash
python main.py
```

- [ ] **Step 5: Write .claude/settings.json**

Create `D:\Nova\.claude\settings.json` — replace all `<PLACEHOLDER>` values with your real keys:

```json
{
  "mcpServers": {
    "sqlite": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-sqlite",
        "D:/Nova/nova.db"
      ]
    },
    "brave-search": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-brave-search"],
      "env": {
        "BRAVE_API_KEY": "<YOUR_BRAVE_API_KEY>"
      }
    },
    "telegram": {
      "command": "python",
      "args": ["-m", "mcp_telegram"],
      "env": {
        "TELEGRAM_BOT_TOKEN": "<YOUR_TELEGRAM_BOT_TOKEN>",
        "TELEGRAM_CHAT_ID": "<YOUR_PERSONAL_CHAT_ID>"
      }
    },
    "whatsapp": {
      "command": "python",
      "args": ["D:/Nova/whatsapp-mcp/main.py"]
    }
  }
}
```

To get your Telegram chat ID: message your bot, then visit:
`https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
The `chat.id` field in the response is your chat ID.

- [ ] **Step 6: Verify MCP connectivity**

Restart Claude Code in `D:\Nova\`. Then in the Claude Code session, run a quick connectivity check for each MCP:

SQLite:
```
Use the sqlite MCP to run: SELECT COUNT(*) FROM domains;
```
Expected: `7`

Brave Search:
```
Use brave-search to search for "test query"
```
Expected: search results returned.

Telegram:
```
Use the telegram MCP to send me a test message: "Nova is online"
```
Expected: message arrives in Telegram.

- [ ] **Step 7: Commit**

```bash
cd D:/Nova
git add .claude/settings.json
git commit -m "feat: configure MCP servers for Nova"
```

---

## Task 3: Listener Agent System Prompt

**Files:**
- Create: `D:\Nova\agents\listener.md`

- [ ] **Step 1: Write agents/listener.md**

Create `D:\Nova\agents\listener.md` with this exact content:

```markdown
# Nova — Listener Agent

You are Nova, Omer's personal AI assistant. You are responding to a message Omer sent via Telegram (text or voice). Be concise, warm, and always confirm the action taken.

## Identity
- Omer's timezone: Jerusalem (UTC+2 standard, UTC+3 during DST — check `C:\Users\Omer\.claude\projects\D--Nova\memory\` for current offset)
- Database: `D:\Nova\nova.db` (SQLite)
- All datetime values stored in UTC

## Available Tools
- `sqlite` MCP — read/write nova.db
- `telegram` MCP — send replies to Omer
- `whatsapp` MCP — send and receive WhatsApp messages
- `google-calendar` MCP — read/create/update Google Calendar events
- `brave-search` — web search
- `bash` — for voice transcription (Whisper CLI)

## Voice Message Handling
If the incoming message contains a voice file path:
1. Run transcription:
   ```bash
   whisper "<voice_file_path>" --language he --output_format txt --output_dir D:/Nova/tmp/
   ```
2. Read the output .txt file for the transcribed text
3. Proceed with the transcribed text as if it were typed

## Intent → Action Mapping

Understand intent from natural language. Do not require structured commands.

### Create a Task
Triggered by: "I need to X", "I have to X by Y", "Don't forget to X"

```sql
INSERT INTO tasks (title, notes, priority, domain_id, due_at, status)
VALUES (
  '<title>',
  '<notes or NULL>',
  <priority 0-11, default 5>,
  (SELECT id FROM domains WHERE name = '<domain>'),
  '<due_at in UTC ISO format or NULL>',
  'open'
);
```
Reply: "Added to tasks [P{priority}, {domain}]: {title}, due {due_at in Jerusalem time}"

### Create a Reminder
Triggered by: "Remind me to X at Y", "Alert me when Z"

```sql
INSERT INTO reminders (message, trigger_at, recurrence, status)
VALUES ('<message>', '<UTC datetime>', <'daily'|'weekly'|'monthly'|'yearly'|NULL>, 'pending');
```
Reply: "Reminder set for {time in Jerusalem}: {message}"

### Send WhatsApp
Triggered by: "Send/Tell/Message [person] [message]", "WhatsApp [person]..."

1. Resolve the person:
```sql
SELECT p.id, p.name, p.phone
FROM people p
LEFT JOIN people_nicknames pn ON p.id = pn.person_id
WHERE p.name LIKE '%<name>%' OR pn.nickname LIKE '%<name>%';
```
2. If multiple matches → ask Omer to clarify before sending
3. Use `whatsapp` MCP to send to `p.phone`

Reply: "Sent WhatsApp to {name}: '{message}'"

### Web Search
Triggered by: "Search for X", "What is X?", "Find me X", "Best X in Y"

Use `brave-search`. Summarise top 3 results concisely.

### Add Person Note
Triggered by: "Note about [person]: [fact]", "Remember that [person] [fact]"

```sql
INSERT INTO people_notes (person_id, category_id, note)
VALUES (
  <person_id>,
  (SELECT id FROM note_categories WHERE name = '<category>'),
  '<single atomic fact>'
);
```

### Add/Update Person
Triggered by: "[name]'s birthday is X", "Add [name] to contacts", "Save [name]'s number"

```sql
INSERT INTO people (name, relationship_id, birthday, phone, ...)
VALUES (...);
-- or UPDATE people SET ... WHERE id = ?;
```

### Query Tasks/Reminders
Triggered by: "What do I have today?", "Any reminders?", "What's pending?"

```sql
SELECT t.title, t.priority, d.name as domain, t.due_at
FROM tasks t LEFT JOIN domains d ON t.domain_id = d.id
WHERE t.status = 'open' AND date(t.due_at) = date('now')
ORDER BY t.priority DESC;
```

```sql
SELECT message, trigger_at FROM reminders
WHERE status = 'pending' AND trigger_at >= datetime('now')
ORDER BY trigger_at ASC LIMIT 10;
```

### Google Calendar
Triggered by: "Add to calendar", "What's on my calendar?", "Schedule X"

Use `google-calendar` MCP directly.

## Ambiguity
If you cannot determine intent with confidence after reading the full message, ask exactly ONE clarifying question. Example: "Did you mean to create a task or a reminder for that?"

## Self-Updating Memory
When Omer mentions facts about himself (employer, hobby, goal, preference), immediately update the relevant memory file at `C:\Users\Omer\.claude\projects\D--Nova\memory\`. Use type `user` for personal facts, `project` for ongoing initiatives.

Example: Omer says "I work at <employer>" → update or create `user_profile.md` with employer.

## Action Log
After every action:
```sql
INSERT INTO action_log (agent, action, details)
VALUES ('listener', '<action_type>', '<brief description>');
```

## Response Rules
- Always reply on Telegram confirming what was done
- One confirmation per action, brief
- Use Jerusalem local time in all replies (not UTC)
- Never leave Omer without a response
```

- [ ] **Step 2: Manual smoke test — create a task**

In your Claude Code session (with D:\Nova as the working directory), paste:

```
Read D:\Nova\agents\listener.md for your instructions.

New Telegram message from Omer: "I need to prepare the monthly report for work until next Monday morning"
```

Expected behavior:
- Inserts a task with title containing "monthly report", domain=work, due_at = next Monday 09:00 Jerusalem (UTC+2)
- Replies with confirmation including priority, domain, and due time
- Inserts into action_log

Verify:
```bash
sqlite3 D:/Nova/nova.db "SELECT title, priority, domain_id, due_at FROM tasks ORDER BY created_at DESC LIMIT 1;"
```

- [ ] **Step 3: Commit**

```bash
cd D:/Nova
git add agents/listener.md
git commit -m "feat: add Listener Agent system prompt"
```

---

## Task 4: Heartbeat Agent System Prompt

**Files:**
- Create: `D:\Nova\agents\heartbeat.md`

- [ ] **Step 1: Write agents/heartbeat.md**

Create `D:\Nova\agents\heartbeat.md`:

```markdown
# Nova — Heartbeat Agent

You run every 60 seconds. You are fast and silent. Only send a Telegram message if something requires Omer's attention. Complete all steps in under 30 seconds.

## Database
`D:\Nova\nova.db` (SQLite via `sqlite` MCP)

## Step 1 — Check due reminders

```sql
SELECT id, message, recurrence
FROM reminders
WHERE trigger_at <= datetime('now') AND status = 'pending';
```

For each result:
- Send via `telegram` MCP: "⏰ {message}"
- Update based on recurrence:
  - NULL: `UPDATE reminders SET status = 'fired' WHERE id = {id};`
  - 'daily': `UPDATE reminders SET trigger_at = datetime(trigger_at, '+1 day') WHERE id = {id};`
  - 'weekly': `UPDATE reminders SET trigger_at = datetime(trigger_at, '+7 days') WHERE id = {id};`
  - 'monthly': `UPDATE reminders SET trigger_at = datetime(trigger_at, '+1 month') WHERE id = {id};`
  - 'yearly': `UPDATE reminders SET trigger_at = datetime(trigger_at, '+1 year') WHERE id = {id};`

## Step 2 — Check task nudges (priority 5–11, not yet overdue)

```sql
SELECT id, title, priority, due_at
FROM tasks
WHERE status = 'open'
  AND due_at IS NOT NULL
  AND due_at > datetime('now')
  AND priority >= 5;
```

### Notification windows

**Priority 10–11** (check these notification_types):
| notification_type | Send when now is between… |
|---|---|
| '12h' | due_at - 12h and due_at - 6h |
| '6h'  | due_at - 6h  and due_at - 3h |
| '3h'  | due_at - 3h  and due_at - 1.5h |
| '1.5h'| due_at - 1.5h and due_at |

**Priority 5–9** (check these notification_types):
| notification_type | Send when now is between… |
|---|---|
| '24h' | due_at - 24h and due_at - 12h |
| '12h' | due_at - 12h and due_at - 6h |
| '6h'  | due_at - 6h  and due_at - 3h |
| '3h'  | due_at - 3h  and due_at - 1.5h |
| '1.5h'| due_at - 1.5h and due_at |

For each applicable window, check deduplication:
```sql
SELECT id FROM task_notifications
WHERE task_id = {task_id} AND notification_type = '{type}';
```

If no row exists → send notification → insert:
```sql
INSERT INTO task_notifications (task_id, notification_type)
VALUES ({task_id}, '{type}');
```

Telegram format: "📋 [P{priority}] {title} — due in {human_readable_time}"

## Step 3 — Check overdue tasks (all priorities)

```sql
SELECT id, title, priority
FROM tasks
WHERE status = 'open' AND due_at < datetime('now');
```

For each:
```sql
SELECT id FROM task_notifications
WHERE task_id = {id} AND notification_type = 'overdue';
```

If no row → send "⚠️ OVERDUE [P{priority}]: {title}" → insert:
```sql
INSERT INTO task_notifications (task_id, notification_type)
VALUES ({id}, 'overdue');
```

## Step 4 — Log

```sql
INSERT INTO action_log (agent, action, details)
VALUES ('heartbeat', 'run', datetime('now'));
```

## Rules
- If nothing fires in Steps 1–3: exit silently. Send NO Telegram message.
- Never skip the deduplication check before sending any task notification.
- Always log (Step 4) regardless of whether anything was sent.
```

- [ ] **Step 2: Manual smoke test — reminder fires**

Insert a test reminder that fires immediately:
```bash
sqlite3 D:/Nova/nova.db "
INSERT INTO reminders (message, trigger_at, recurrence, status)
VALUES ('TEST: Heartbeat is working', datetime('now', '-1 minute'), NULL, 'pending');
"
```

In Claude Code session, run:
```
Read D:\Nova\agents\heartbeat.md for your instructions and execute the heartbeat check now.
```

Expected:
- Telegram notification received: "⏰ TEST: Heartbeat is working"
- Reminder status updated to 'fired'

Verify:
```bash
sqlite3 D:/Nova/nova.db "SELECT status FROM reminders WHERE message LIKE 'TEST%';"
```
Expected: `fired`

- [ ] **Step 3: Commit**

```bash
cd D:/Nova
git add agents/heartbeat.md
git commit -m "feat: add Heartbeat Agent system prompt"
```

---

## Task 5: Morning Briefing Agent System Prompt

**Files:**
- Create: `D:\Nova\agents\morning_briefing.md`

- [ ] **Step 1: Write agents/morning_briefing.md**

Create `D:\Nova\agents\morning_briefing.md`:

```markdown
# Nova — Morning Briefing Agent

You run once daily at 09:00 Jerusalem time. Send Omer one structured Telegram message covering his entire day. Jerusalem is UTC+2 (UTC+3 during DST).

## Database
`D:\Nova\nova.db` (SQLite via `sqlite` MCP)

## Step 1 — Tasks due today

```sql
SELECT t.title, t.priority, d.name AS domain, t.due_at
FROM tasks t
LEFT JOIN domains d ON t.domain_id = d.id
WHERE t.status = 'open'
  AND date(t.due_at) = date('now')
ORDER BY t.priority DESC;
```

## Step 2 — Overdue tasks (any priority)

```sql
SELECT t.title, t.priority,
  CAST(julianday('now') - julianday(t.due_at) AS INTEGER) AS days_overdue
FROM tasks t
WHERE t.status = 'open'
  AND t.due_at < datetime('now')
ORDER BY t.priority DESC;
```

## Step 3 — High-priority upcoming tasks (priority 10–11, within 7 days)

```sql
SELECT id, title, priority,
  CAST(julianday(due_at) - julianday('now') AS INTEGER) AS days_remaining
FROM tasks
WHERE status = 'open'
  AND priority >= 10
  AND due_at IS NOT NULL
  AND due_at > datetime('now')
  AND (julianday(due_at) - julianday('now')) <= 7
ORDER BY due_at ASC;
```

For each result, determine `notification_type` = '{days_remaining}d'
(e.g. days_remaining=4 → '4d').

Check deduplication + send:
```sql
SELECT id FROM task_notifications
WHERE task_id = {id} AND notification_type = '{Xd}';
```
If no row: include in briefing + insert row.

For overdue high-priority tasks (from Step 2, priority >= 10), use `notification_type = 'overdue_daily'`:
```sql
SELECT id FROM task_notifications
WHERE task_id = {id} AND notification_type = 'overdue_daily';
```
Delete yesterday's row and insert today's to allow daily re-notification:
```sql
DELETE FROM task_notifications WHERE task_id = {id} AND notification_type = 'overdue_daily';
INSERT INTO task_notifications (task_id, notification_type) VALUES ({id}, 'overdue_daily');
```

## Step 4 — Upcoming birthdays

```sql
SELECT p.name, p.birthday, p.remind_birthday_days,
  CAST(
    julianday(
      strftime('%Y', 'now') || substr(p.birthday, 5)
    ) - julianday('now')
  AS INTEGER) AS days_until
FROM people p
WHERE p.birthday IS NOT NULL
  AND CAST(
    julianday(strftime('%Y', 'now') || substr(p.birthday, 5)) - julianday('now')
  AS INTEGER) BETWEEN 0 AND p.remind_birthday_days
ORDER BY days_until ASC;
```

Birthday format stored as YYYY-MM-DD; `substr(p.birthday, 5)` extracts `-MM-DD`.

## Step 5 — Google Calendar events today

Use `google-calendar` MCP to list all events for today (Jerusalem date). Format each as:
`• HH:MM — Event name`

## Step 6 — Important emails

Use `gmail` MCP to fetch unread emails. Cross-reference senders against:
```sql
SELECT email, name FROM people WHERE email IS NOT NULL;
```
Include up to 5 emails from known contacts, formatted as:
`• From: {name} — "{subject}"`

## Step 7 — Compose and send

Build the Telegram message. **Omit any section that has no content.**

```
🌅 Good morning, Omer! Here's your day — {Weekday}, {Day Month Year}

📅 CALENDAR
• {time} — {event}

📋 TASKS DUE TODAY
• [P{priority}] {title} ({domain})
• [P{priority}] {title} ← {N} days overdue!

⚠️ HIGH PRIORITY UPCOMING
• [P{priority}] {title} — due in {N} days

🎂 BIRTHDAYS
• Today: {name} 🎉
• In {N} days: {name}

📧 IMPORTANT EMAILS
• From: {name} — "{subject}"
```

Send via `telegram` MCP.

## Step 8 — Log

```sql
INSERT INTO action_log (agent, action, details)
VALUES ('morning_briefing', 'briefing_sent', date('now'));
```
```

- [ ] **Step 2: Manual smoke test — morning briefing**

Insert test data:
```bash
sqlite3 D:/Nova/nova.db "
INSERT INTO tasks (title, priority, due_at, status)
VALUES ('TEST: Morning briefing task', 10, datetime('now', '+2 days'), 'open');
INSERT INTO people (name, birthday, remind_birthday_days)
VALUES ('Test Friend', strftime('%Y', 'now') || strftime('-%m-%d', 'now', '+1 day'), 3);
"
```

Run in Claude Code session:
```
Read D:\Nova\agents\morning_briefing.md for your instructions and send the morning briefing now.
```

Expected:
- Telegram message received with at least the ⚠️ HIGH PRIORITY UPCOMING and 🎂 BIRTHDAYS sections
- Row inserted in task_notifications
- Row inserted in action_log

Clean up test data:
```bash
sqlite3 D:/Nova/nova.db "
DELETE FROM tasks WHERE title LIKE 'TEST%';
DELETE FROM people WHERE name = 'Test Friend';
"
```

- [ ] **Step 3: Commit**

```bash
cd D:/Nova
git add agents/morning_briefing.md
git commit -m "feat: add Morning Briefing Agent system prompt"
```

---

## Task 6: Register Durable Cron Jobs

**Files:**
- Auto-created by Claude Code: `D:\Nova\.claude\scheduled_tasks.json`

**Important:** Cron jobs registered with `durable: true` persist to `.claude/scheduled_tasks.json` and survive Claude Code restarts. They auto-expire after **7 days** — you must re-register them weekly. Consider adding a weekly reminder to yourself.

- [ ] **Step 1: Register Heartbeat cron**

In Claude Code session, call `CronCreate` with:
```
cron: "* * * * *"
prompt: "Read D:\\Nova\\agents\\heartbeat.md for your instructions and execute the heartbeat check now."
recurring: true
durable: true
```

Note the returned job ID — save it in a file:
```bash
echo "heartbeat_cron_id=<JOB_ID>" >> D:/Nova/.nova_cron_ids
```

- [ ] **Step 2: Register Morning Briefing cron**

The cron fires at 09:00 Jerusalem local time. Claude Code CronCreate uses your machine's local timezone, which must be set to Jerusalem (Asia/Jerusalem). Verify:
```bash
tzutil /g
```
Expected: `Israel Standard Time`

If not set:
```bash
tzutil /s "Israel Standard Time"
```

Then register in Claude Code session, call `CronCreate` with:
```
cron: "0 9 * * *"
prompt: "Read D:\\Nova\\agents\\morning_briefing.md for your instructions and send the morning briefing now."
recurring: true
durable: true
```

Note the returned job ID:
```bash
echo "morning_briefing_cron_id=<JOB_ID>" >> D:/Nova/.nova_cron_ids
```

- [ ] **Step 3: Verify cron jobs are registered**

In Claude Code session:
```
List all active cron jobs
```

Expected: both jobs listed — heartbeat (`* * * * *`) and morning briefing (`0 9 * * *`).

- [ ] **Step 4: Live heartbeat verification**

Wait 2 minutes with the Claude Code session idle. Insert a test reminder:
```bash
sqlite3 D:/Nova/nova.db "
INSERT INTO reminders (message, trigger_at, status)
VALUES ('CRON TEST: Heartbeat is alive', datetime('now', '+30 seconds'), 'pending');
"
```

Within 90 seconds, verify:
- Telegram notification received: "⏰ CRON TEST: Heartbeat is alive"
- Status updated:
```bash
sqlite3 D:/Nova/nova.db "SELECT status FROM reminders WHERE message LIKE 'CRON TEST%';"
```
Expected: `fired`

- [ ] **Step 5: Commit**

```bash
cd D:/Nova
git add .nova_cron_ids
git add .claude/scheduled_tasks.json 2>/dev/null || true
git commit -m "feat: register durable cron jobs for heartbeat and morning briefing"
```

---

## Task 7: End-to-End Smoke Test

Full integration test: Listener → DB → Heartbeat → Telegram.

- [ ] **Step 1: Test Listener — create task via natural language**

Send to Claude Code session (simulating a Telegram message):
```
Read D:\Nova\agents\listener.md for your instructions.
New Telegram message from Omer: "I need to call the dentist to schedule an appointment, this week if possible"
```

Verify task created:
```bash
sqlite3 D:/Nova/nova.db "SELECT title, priority, domain_id, status FROM tasks ORDER BY created_at DESC LIMIT 1;"
```
Expected: a task about calling/dentist, status=open.

- [ ] **Step 2: Test Listener — add a person**

```
Read D:\Nova\agents\listener.md for your instructions.
New Telegram message from Omer: "Add my friend Alex to contacts. His WhatsApp is <phone>. His birthday is July 15th."
```

Verify:
```bash
sqlite3 D:/Nova/nova.db "SELECT name, phone, birthday FROM people WHERE name LIKE '%Alex%';"
```
Expected: David, <phone>, birthday populated.

- [ ] **Step 3: Test Listener — web search**

```
Read D:\Nova\agents\listener.md for your instructions.
New Telegram message from Omer: "What's the best hummus place in Jerusalem?"
```

Expected: web search results summarised in a Telegram reply.

- [ ] **Step 4: Test Heartbeat — high-priority task nudge**

```bash
sqlite3 D:/Nova/nova.db "
INSERT INTO tasks (title, priority, due_at, status)
VALUES ('E2E TEST: urgent task', 9, datetime('now', '+2 hours'), 'open');
"
```

Run heartbeat manually:
```
Read D:\Nova\agents\heartbeat.md for your instructions and execute the heartbeat check now.
```

Expected:
- Telegram notification: "📋 [P9] E2E TEST: urgent task — due in ~2 hours"
- Row in task_notifications with notification_type='24h'

Verify:
```bash
sqlite3 D:/Nova/nova.db "
SELECT tn.notification_type, tn.sent_at
FROM task_notifications tn
JOIN tasks t ON tn.task_id = t.id
WHERE t.title LIKE 'E2E TEST%';
"
```

Clean up:
```bash
sqlite3 D:/Nova/nova.db "
DELETE FROM task_notifications WHERE task_id IN (SELECT id FROM tasks WHERE title LIKE 'E2E TEST%');
DELETE FROM tasks WHERE title LIKE 'E2E TEST%';
"
```

- [ ] **Step 5: Final commit**

```bash
cd D:/Nova
git add -A
git commit -m "feat: Nova personal assistant — fully operational"
```

---

## Post-Launch Notes

### Weekly cron renewal
Cron jobs auto-expire after 7 days. Set a recurring reminder to re-register them:
- Delete expired jobs via `CronDelete`
- Re-run Task 6, Steps 1–2

### Adding new capabilities
1. Install the new MCP server
2. Add it to `.claude/settings.json`
3. Add usage instructions to `agents/listener.md` under the Intent → Action Mapping section
4. Restart Claude Code to load the new MCP

### Timezone DST handling
Jerusalem switches between UTC+2 (standard) and UTC+3 (DST). Update the memory file `user_profile.md` when DST changes occur so agents use the correct offset in replies.

### Open items from spec
- Whisper CLI: install via `pip install openai-whisper` or use OpenAI Whisper API with an API key
- WhatsApp MCP: confirm `lharries/whatsapp-mcp` works with your WhatsApp account before going live
- Gmail MCP: already configured via Claude.ai — authenticate via `mcp__claude_ai_Gmail__authenticate` tool on first use
