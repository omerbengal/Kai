# Nova — Personal AI Assistant: Design Spec
**Date:** 2026-04-10  
**Status:** Approved

---

## 1. Overview

Nova is a 24/7 personal AI assistant built on Claude Code. It listens for commands via Telegram (text and voice), executes actions on the user's behalf (web search, WhatsApp, Google Calendar, etc.), proactively sends reminders and task nudges, and delivers a daily morning briefing. All structured data is persisted in a local SQLite database. User profile and preferences live in file-based memory.

**User:** Omer (Jerusalem timezone, UTC+2)

---

## 2. Architecture

Three stateless agents share a single SQLite database (`D:\Nova\nova.db`) as the source of truth.

```
┌─────────────────────────────────────────────────────────┐
│                     OMER (Telegram)                      │
│              text messages / voice messages              │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
          ┌──────────────────────────┐
          │   Telegram MCP Channel   │  ← always listening
          └──────────────┬───────────┘
                         │ triggers
                         ▼
          ┌──────────────────────────┐
          │   Listener Agent         │  spawned per message
          │  - transcribe voice      │
          │  - understand intent     │
          │  - execute actions       │
          │  - write to SQLite       │
          │  - reply via Telegram    │
          └──────────────────────────┘
                         │
                         │ reads/writes
                         ▼
          ┌──────────────────────────┐
          │       SQLite DB          │  D:\Nova\nova.db
          │  tasks, reminders,       │
          │  people, notifications   │
          └──────────────┬───────────┘
                    ▲    │ reads
                    │    │
          ┌─────────┴────────────────┐
          │   Heartbeat Agent        │  spawned every 60s
          │  - checks due reminders  │
          │  - checks task nudges    │
          │  - sends Telegram alerts │
          │  - marks items fired     │
          └──────────────────────────┘
          ┌──────────────────────────┐
          │   Morning Briefing Agent │  spawned at 07:00 UTC (09:00 Jerusalem)
          │  - daily summary         │
          │  - high-priority tasks   │
          │  - birthdays             │
          │  - calendar + email      │
          └──────────────────────────┘

          ┌──────────────────────────┐
          │   File-based Memory      │  C:\Users\Omer\.claude\projects\D--Nova\memory\
          │  preferences, profile,   │  loaded by all agents at startup
          │  user context            │
          └──────────────────────────┘
```

### 2.1 Agent Summary

| Agent | Trigger | Responsibility |
|---|---|---|
| Listener | Incoming Telegram message | Understand intent, execute actions, reply |
| Heartbeat | CronCreate every 60s | Check due reminders + task nudges, notify |
| Morning Briefing | CronCreate at 07:00 UTC | Daily digest — calendar, tasks, birthdays, email |

---

## 3. SQLite Schema

File: `D:\Nova\schema.sql`

```sql
-- Lookup tables
CREATE TABLE relationship_types (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE    -- 'family', 'friend', 'work', 'acquaintance', etc.
);

CREATE TABLE domains (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE    -- 'work', 'hobbies', 'family', 'friends', 'health', etc.
);

CREATE TABLE note_categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE    -- 'work', 'preferences', 'history', 'family', 'health', etc.
);

-- People
CREATE TABLE people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    relationship_id INTEGER REFERENCES relationship_types(id),
    birthday DATE,                       -- YYYY-MM-DD, NULL if unknown
    remind_birthday_days INTEGER DEFAULT 3,
    phone TEXT,                          -- E.164 format for WhatsApp
    telegram_username TEXT,
    email TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE people_nicknames (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    nickname TEXT NOT NULL
);

CREATE TABLE people_notes (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES people(id),
    category_id INTEGER NOT NULL REFERENCES note_categories(id),
    note TEXT NOT NULL,                  -- ONE atomic fact per row
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Reminders
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY,
    message TEXT NOT NULL,
    trigger_at DATETIME NOT NULL,
    recurrence TEXT CHECK (recurrence IN ('daily', 'weekly', 'monthly', 'yearly')),
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'fired', 'dismissed')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Tasks
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    notes TEXT,
    priority INTEGER DEFAULT 5 CHECK (priority BETWEEN 0 AND 11),
    domain_id INTEGER REFERENCES domains(id),
    due_at DATETIME,
    status TEXT DEFAULT 'open' CHECK (status IN ('open', 'done', 'cancelled')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Task notification deduplication log
CREATE TABLE task_notifications (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    notification_type TEXT NOT NULL,
    -- Priority 10-11 morning briefing: '7d','6d','5d','4d','3d','2d','1d','overdue_daily'
    -- Priority 10-11 heartbeat:        '12h','6h','3h','1.5h'
    -- Priority 5-9 heartbeat:          '24h','12h','6h','3h','1.5h'
    -- All priorities heartbeat:        'overdue'
    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(task_id, notification_type)
);

-- Agent action log (audit trail)
CREATE TABLE action_log (
    id INTEGER PRIMARY KEY,
    agent TEXT NOT NULL CHECK (agent IN ('listener', 'heartbeat', 'morning_briefing')),
    action TEXT NOT NULL,
    details TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 3.1 Seed Data (`seed.sql`)

Initial lookup values to be inserted on first run:

```sql
INSERT INTO relationship_types (name) VALUES
  ('family'), ('friend'), ('work'), ('acquaintance'), ('other');

INSERT INTO domains (name) VALUES
  ('work'), ('hobbies'), ('family'), ('friends'), ('health'), ('finance'), ('personal');

INSERT INTO note_categories (name) VALUES
  ('work'), ('preferences'), ('history'), ('family'), ('health'),
  ('personality'), ('goals'), ('other');
```

---

## 4. Task Nudge Matrix

Priority determines both which agent sends nudges and how far in advance.

| Priority | Morning Briefing | Heartbeat |
|---|---|---|
| 10–11 | 7,6,5,4,3,2,1 days before due + every morning after overdue | 12h, 6h, 3h, 1.5h before due + overdue |
| 5–9 | — | 24h, 12h, 6h, 3h, 1.5h before due + overdue |
| 0–4 | — | overdue only |

**Deduplication:** Before sending any nudge, the agent checks `task_notifications` for an existing row with the matching `(task_id, notification_type)`. If found, skip. If not found, send and insert.

---

## 5. Listener Agent

**File:** `D:\Nova\agents\listener.md`

**Trigger:** Incoming Telegram message (text or voice).

**Flow:**
1. If voice message → transcribe via Whisper API → text
2. Load memory files (timezone, user profile, preferences)
3. Understand intent from natural language (no commands required)
4. Execute action(s)
5. Write results to SQLite if needed
6. Update memory files if new user facts are learned
7. Reply on Telegram confirming what was done

**Intent → Action mapping (examples):**

| Natural language | Action |
|---|---|
| "I need to finish the report until tomorrow morning" | INSERT task, due_at = tomorrow 09:00 |
| "Don't forget — Alex's moving next Saturday" | INSERT reminder for next Saturday |
| "Oh I just remembered, Sam's birthday is March 3rd" | UPDATE people SET birthday for Sam |
| "Send WhatsApp to Dana: running 10 min late" | Look up Dana in people → send WhatsApp |
| "What's the best sushi in Tel Aviv?" | Web search → reply |
| "I want to learn guitar this year" | INSERT task, domain=hobbies, no due date |
| "I work at <employer>" | Update memory file with employer |

**Ambiguity handling:** If intent cannot be determined with confidence, ask one targeted clarifying question before acting.

**Self-updating memory:** When the Listener learns new facts about Omer (employer, preferences, life context), it immediately writes/updates the appropriate memory file.

---

## 6. Heartbeat Agent

**File:** `D:\Nova\agents\heartbeat.md`

**Trigger:** CronCreate, every 60 seconds.

**Design principle:** Fast and cheap. Minimal context, a handful of SQL queries, notify if needed, exit.

**Flow:**
1. Load timezone from memory (Jerusalem, UTC+2)
2. Run SQL checks:
   - Reminders: `WHERE trigger_at <= NOW() AND status = 'pending'`
   - Tasks priority 5-11: check heartbeat nudge windows
   - Tasks priority 0-4: check if overdue
3. For each item due:
   - Check `task_notifications` for duplicates
   - Send Telegram notification
   - Mark reminder as `fired` (or insert `task_notifications` row)
   - For recurring reminders: calculate next `trigger_at`, reset to `pending`
4. Log run to `action_log`
5. Exit (no message sent if nothing is due)

**Silent by default:** Only messages Omer when there is something actionable.

---

## 7. Morning Briefing Agent

**File:** `D:\Nova\agents\morning_briefing.md`

**Trigger:** CronCreate at 07:00 UTC (09:00 Jerusalem) daily.

**Output format:**
```
🌅 Good morning, Omer! Here's your day — [Weekday, Date]

📅 CALENDAR (Today)
  • HH:MM — Event name

📋 TASKS DUE TODAY / OVERDUE
  • [P10] Task title ← X days overdue!
  • [P7]  Task title

⚠️ HIGH PRIORITY UPCOMING (priority 10-11, within 7 days)
  • [P11] Task title — due in X days

🎂 BIRTHDAYS
  • Today: Person's name 🎉
  • In X days: Person's name

📧 IMPORTANT EMAILS
  • From: Sender — "Subject"
```

Empty sections are omitted. Message is kept concise.

---

## 8. MCP Stack

| MCP Server | Used by | Purpose |
|---|---|---|
| `telegram-mcp` | All agents | Receive messages, send notifications |
| `whatsapp-mcp` | Listener | Send and receive WhatsApp messages |
| `google-calendar-mcp` | Listener + Morning Briefing | Read/create/update events |
| `gmail-mcp` | Morning Briefing | Read important emails |
| `sqlite-mcp` | All agents | Read/write `nova.db` |
| `whisper-mcp` | Listener | Transcribe voice messages |
| `web-search` | Listener | Search the web |

**Extensibility:** New capabilities are added as additional MCP servers. The Listener Agent gains access automatically — no architectural changes required.

---

## 9. Memory System

Two layers:

| Layer | Location | Contains |
|---|---|---|
| File-based memory | `C:\Users\Omer\.claude\projects\D--Nova\memory\` | User profile, preferences, timezone, hobbies, life goals, employer, context |
| SQLite | `D:\Nova\nova.db` | Structured actionable data — people, tasks, reminders, notifications |

The Listener Agent reads memory files on every invocation and writes updates when new facts are learned.

---

## 10. Project Structure

```
D:\Nova\
  nova.db                        ← SQLite database (runtime)
  schema.sql                     ← DB schema (source of truth)
  seed.sql                       ← Initial lookup table values
  agents/
    listener.md                  ← Listener Agent system prompt
    heartbeat.md                 ← Heartbeat Agent system prompt
    morning_briefing.md          ← Morning Briefing Agent system prompt
  docs/
    superpowers/
      specs/
        2026-04-10-nova-personal-assistant-design.md  ← this file
```

---

## 11. Open Questions / Future Scope

- Voice transcription: Whisper API (OpenAI) is assumed — confirm API key setup
- WhatsApp MCP: confirm which MCP server implementation to use
- Gmail MCP: confirm read scope (all unread vs. flagged vs. from known contacts only)
- Future integrations: Spotify, Home Assistant, Notion — slot in as new MCP servers
