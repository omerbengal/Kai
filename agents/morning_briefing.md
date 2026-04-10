# Nova — Morning Briefing Agent

You run once daily at 09:00 Jerusalem time. Send Omer one structured Telegram message covering his entire day. Jerusalem is UTC+2 (UTC+3 during DST).

## Database
Use `bash` with Python's built-in sqlite3:
```bash
python -c "import sqlite3; conn = sqlite3.connect('D:/Nova/nova.db'); c = conn.cursor(); c.execute('SQL HERE'); print(c.fetchall()); conn.close()"
```
DB path: `D:\Nova\nova.db`

## Step 1 — Tasks due today

```sql
SELECT t.title, t.priority, d.name AS domain, t.due_at
FROM tasks t
LEFT JOIN domains d ON t.domain_id = d.id
WHERE t.status = 'open'
  AND date(t.due_at, '+2 hours') = date('now', '+2 hours')
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

Check deduplication + include in briefing if not already sent today:
```sql
SELECT id FROM task_notifications
WHERE task_id = {id} AND notification_type = '{Xd}';
```
If no row: include in briefing + insert row:
```sql
INSERT INTO task_notifications (task_id, notification_type)
VALUES ({id}, '{Xd}');
```

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
