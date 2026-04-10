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
