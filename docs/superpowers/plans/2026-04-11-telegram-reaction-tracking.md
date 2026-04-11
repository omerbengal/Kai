# Telegram Reaction Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When Omer reacts to a Telegram message, log the emoji in `messages.reactions` and mark the corresponding reminder/task as handled; un-reacting reverses the action.

**Architecture:** The Telegram plugin's `server.ts` is patched (via `patch_telegram_plugin.py`) to add a `bot.on('message_reaction', ...)` handler that calls a new Python script `handle_reaction.py` via `child_process.exec`. The script does all DB work — no Claude Code session woken up.

**Tech Stack:** Python 3.9, SQLite3, TypeScript/grammy (patched via string replacement), Telegram Bot API

---

## File Map

| File | Change |
|---|---|
| `D:/Kai/kai.db` | `ALTER TABLE messages ADD COLUMN reactions TEXT` |
| `D:/Kai/scripts/handle_reaction.py` | New script — all reaction DB logic |
| `D:/Kai/scripts/patch_telegram_plugin.py` | Add two new patch functions (exec import + reaction handler) |
| `D:/Kai/tests/test_handle_reaction.py` | New test file |

---

## Task 1: DB Migration

**Files:**
- Modify: `D:/Kai/kai.db` (runtime migration, no source file)

- [ ] **Step 1: Run migration**

```bash
python -c "
import sqlite3
conn = sqlite3.connect('D:/Kai/kai.db')
conn.execute(\"ALTER TABLE messages ADD COLUMN reactions TEXT\")
conn.commit()
conn.close()
print('done')
"
```

- [ ] **Step 2: Verify**

```bash
python -c "
import sqlite3
conn = sqlite3.connect('D:/Kai/kai.db')
cur = conn.cursor()
cur.execute(\"PRAGMA table_info(messages)\")
for r in cur.fetchall():
    print(r)
conn.close()
"
```

Expected: a row with `name='reactions'` and `type='TEXT'`.

- [ ] **Step 3: Commit migration note**

```bash
cd D:/Kai && git add -A && git commit -m "feat: add reactions column to messages table"
```

---

## Task 2: `handle_reaction.py` — Core + Reactions Logging

**Files:**
- Create: `D:/Kai/scripts/handle_reaction.py`
- Create: `D:/Kai/tests/test_handle_reaction.py`

- [ ] **Step 1: Create test file with basic tests**

Create `D:/Kai/tests/test_handle_reaction.py`:

```python
"""Tests for handle_reaction.py — run with: python -m pytest D:/Kai/tests/test_handle_reaction.py -v"""
import json
import os
import sqlite3
import sys
import tempfile

import pytest

# Add scripts dir to path
sys.path.insert(0, 'D:/Kai/scripts')


def make_db(tmp_path):
    """Create a temp DB with the messages schema."""
    db_path = str(tmp_path / 'test.db')
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_message_id INTEGER,
            direction TEXT NOT NULL,
            text TEXT,
            reactions TEXT,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE reminders (
            id INTEGER PRIMARY KEY,
            message TEXT NOT NULL,
            trigger_at DATETIME NOT NULL,
            recurrence TEXT,
            status TEXT DEFAULT 'pending'
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            priority INTEGER DEFAULT 5,
            status TEXT DEFAULT 'open'
        );
        CREATE TABLE action_log (
            id INTEGER PRIMARY KEY,
            agent TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()
    return db_path


def test_reaction_added_to_messages(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (42, 'outgoing', 'hello')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_add(message_id=42, reaction='👍', chat_id='123')

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT reactions FROM messages WHERE telegram_message_id = 42").fetchone()
    conn.close()
    assert json.loads(row[0]) == ['👍']


def test_second_reaction_appended(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text, reactions) VALUES (42, 'outgoing', 'hello', '[\"❤️\"]')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_add(message_id=42, reaction='👍', chat_id='123')

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT reactions FROM messages WHERE telegram_message_id = 42").fetchone()
    conn.close()
    assert set(json.loads(row[0])) == {'❤️', '👍'}


def test_message_not_found_raises(tmp_path):
    db = make_db(tmp_path)
    import handle_reaction
    handle_reaction.DB_PATH = db
    with pytest.raises(handle_reaction.MessageNotFound):
        handle_reaction.handle_add(message_id=999, reaction='👍', chat_id='123')
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd D:/Kai && python -m pytest tests/test_handle_reaction.py -v 2>&1 | head -30
```

Expected: `ImportError` or `ModuleNotFoundError` (script doesn't exist yet).

- [ ] **Step 3: Create `handle_reaction.py` with core logic**

Create `D:/Kai/scripts/handle_reaction.py`:

```python
#!/usr/bin/env python3
"""
Handle Telegram emoji reactions.

Usage:
  python handle_reaction.py --chat_id ${KAI_TELEGRAM_CHAT_ID} --message_id 123 --reaction 👍 [--mode add|remove]

Called by the Telegram plugin (server.ts) via child_process.exec.
"""
import argparse
import json
import sqlite3
import sys
import io
import urllib.request
from typing import Optional

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

DB_PATH = 'D:/Kai/kai.db'
BOT_TOKEN = '${KAI_TELEGRAM_BOT_TOKEN}'
CHAT_ID = '${KAI_TELEGRAM_CHAT_ID}'


class MessageNotFound(Exception):
    pass


def send_telegram_error(chat_id: str, text: str) -> None:
    try:
        url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
        payload = json.dumps({'chat_id': chat_id, 'text': text}).encode()
        req = urllib.request.Request(url, payload, {'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f'[handle_reaction] failed to send error to Telegram: {e}', file=sys.stderr)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = str
    conn.row_factory = sqlite3.Row
    return conn


def lookup_message(conn: sqlite3.Connection, message_id: int) -> sqlite3.Row:
    row = conn.execute(
        'SELECT id, text, reactions, direction FROM messages WHERE telegram_message_id = ?',
        (message_id,)
    ).fetchone()
    if row is None:
        raise MessageNotFound(f'No message with telegram_message_id={message_id}')
    return row


def update_reactions_add(conn: sqlite3.Connection, msg_id: int, current: Optional[str], emoji: str) -> None:
    reactions = json.loads(current) if current else []
    if emoji not in reactions:
        reactions.append(emoji)
    conn.execute('UPDATE messages SET reactions = ? WHERE id = ?', (json.dumps(reactions), msg_id))


def update_reactions_remove(conn: sqlite3.Connection, msg_id: int, current: Optional[str], emoji: str) -> None:
    reactions = json.loads(current) if current else []
    if emoji in reactions:
        reactions.remove(emoji)
    conn.execute(
        'UPDATE messages SET reactions = ? WHERE id = ?',
        (json.dumps(reactions) if reactions else None, msg_id)
    )


def log_action(conn: sqlite3.Connection, action: str, details: str) -> None:
    conn.execute(
        "INSERT INTO action_log (agent, action, details) VALUES ('listener', ?, ?)",
        (action, details)
    )


def handle_add(message_id: int, reaction: str, chat_id: str) -> None:
    conn = get_connection()
    try:
        msg = lookup_message(conn, message_id)
        update_reactions_add(conn, msg['id'], msg['reactions'], reaction)
        _apply_add_action(conn, msg, reaction)
        log_action(conn, 'reaction_add', f'msg={message_id} emoji={reaction}')
        conn.commit()
    finally:
        conn.close()


def handle_remove(message_id: int, reaction: str, chat_id: str) -> None:
    conn = get_connection()
    try:
        msg = lookup_message(conn, message_id)
        update_reactions_remove(conn, msg['id'], msg['reactions'], reaction)
        _apply_remove_action(conn, msg, reaction)
        log_action(conn, 'reaction_remove', f'msg={message_id} emoji={reaction}')
        conn.commit()
    finally:
        conn.close()


def _apply_add_action(conn: sqlite3.Connection, msg: sqlite3.Row, reaction: str) -> None:
    """Mark reminder/task as done when user reacts."""
    text = msg['text'] or ''
    msg_type, content = _parse_message_type(text)
    if msg_type == 'reminder' and content:
        _mark_reminder(conn, content, 'dismissed')
    elif msg_type == 'task' and content:
        _mark_task(conn, content, 'done')


def _apply_remove_action(conn: sqlite3.Connection, msg: sqlite3.Row, reaction: str) -> None:
    """Reverse reminder/task action when user removes reaction."""
    text = msg['text'] or ''
    msg_type, content = _parse_message_type(text)
    if msg_type == 'reminder' and content:
        _mark_reminder(conn, content, 'fired')
    elif msg_type == 'task' and content:
        _mark_task(conn, content, 'open')


def _parse_message_type(text: str):
    """
    Returns (type, content) where type is 'reminder', 'task', or 'unknown'.
    For reminders: content = reminder message text.
    For tasks: content = task title.
    """
    import re
    if text.startswith('⏰ '):
        return 'reminder', text[len('⏰ '):]
    m = re.match(r'^📋 \[P\d+\] (.+?) — due in ~[\d.]+h$', text)
    if m:
        return 'task', m.group(1)
    m = re.match(r'^⚠️ OVERDUE \[P\d+\]: (.+)$', text)
    if m:
        return 'task', m.group(1)
    return 'unknown', None


def _mark_reminder(conn: sqlite3.Connection, message_text: str, new_status: str) -> None:
    """Mark one-shot reminder with matching message text; skip recurring ones on 'dismissed'."""
    row = conn.execute(
        'SELECT id, recurrence, status FROM reminders WHERE message = ?',
        (message_text,)
    ).fetchone()
    if row is None:
        return
    # For 'dismissed': only apply to one-shot reminders (recurrence IS NULL)
    if new_status == 'dismissed' and row['recurrence'] is not None:
        return
    conn.execute('UPDATE reminders SET status = ? WHERE id = ?', (new_status, row['id']))


def _mark_task(conn: sqlite3.Connection, title: str, new_status: str) -> None:
    """Mark matching task with the given status."""
    conn.execute(
        'UPDATE tasks SET status = ? WHERE title = ? AND status != ?',
        (new_status, title, new_status)
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chat_id', required=True)
    parser.add_argument('--message_id', type=int, required=True)
    parser.add_argument('--reaction', required=True)
    parser.add_argument('--mode', choices=['add', 'remove'], default='add')
    args = parser.parse_args()

    try:
        if args.mode == 'add':
            handle_add(args.message_id, args.reaction, args.chat_id)
        else:
            handle_remove(args.message_id, args.reaction, args.chat_id)
    except MessageNotFound as e:
        send_telegram_error(args.chat_id, f'❌ תגובה: לא נמצאה הודעה {args.message_id} ב-DB')
        print(f'[handle_reaction] {e}', file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        send_telegram_error(args.chat_id, f'❌ שגיאה בטיפול בתגובה: {e}')
        print(f'[handle_reaction] unexpected error: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Run core tests — verify they pass**

```bash
cd D:/Kai && python -m pytest tests/test_handle_reaction.py::test_reaction_added_to_messages tests/test_handle_reaction.py::test_second_reaction_appended tests/test_handle_reaction.py::test_message_not_found_raises -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
cd D:/Kai && git add scripts/handle_reaction.py tests/test_handle_reaction.py && git commit -m "feat: add handle_reaction.py with core reactions logging"
```

---

## Task 3: Reminder Handling (Add + Remove)

**Files:**
- Modify: `D:/Kai/tests/test_handle_reaction.py` (add tests)
- No changes to `handle_reaction.py` — logic already implemented in Task 2

- [ ] **Step 1: Add reminder tests**

Append to `D:/Kai/tests/test_handle_reaction.py`:

```python
def test_reaction_on_oneshot_reminder_dismisses(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (10, 'outgoing', '⏰ לשלוח הודעה לרופא')")
    conn.execute("INSERT INTO reminders (message, trigger_at, recurrence, status) VALUES ('לשלוח הודעה לרופא', '2026-04-11 17:00:00', NULL, 'fired')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_add(message_id=10, reaction='👍', chat_id='123')

    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM reminders WHERE message = 'לשלוח הודעה לרופא'").fetchone()[0]
    conn.close()
    assert status == 'dismissed'


def test_reaction_on_recurring_reminder_does_not_dismiss(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (11, 'outgoing', '⏰ לשתות מים')")
    conn.execute("INSERT INTO reminders (message, trigger_at, recurrence, status) VALUES ('לשתות מים', '2026-04-11 09:00:00', 'daily', 'pending')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_add(message_id=11, reaction='✅', chat_id='123')

    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM reminders WHERE message = 'לשתות מים'").fetchone()[0]
    conn.close()
    assert status == 'pending'  # unchanged


def test_unreact_on_reminder_reverts_to_pending(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text, reactions) VALUES (12, 'outgoing', '⏰ לשלוח הודעה לרופא', '[\"👍\"]')")
    conn.execute("INSERT INTO reminders (message, trigger_at, recurrence, status) VALUES ('לשלוח הודעה לרופא', '2026-04-11 17:00:00', NULL, 'dismissed')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_remove(message_id=12, reaction='👍', chat_id='123')

    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM reminders WHERE message = 'לשלוח הודעה לרופא'").fetchone()[0]
    conn.close()
    assert status == 'pending'  # reverts to pending so heartbeat can re-fire it
```

- [ ] **Step 2: Run reminder tests**

```bash
cd D:/Kai && python -m pytest tests/test_handle_reaction.py::test_reaction_on_oneshot_reminder_dismisses tests/test_handle_reaction.py::test_reaction_on_recurring_reminder_does_not_dismiss tests/test_handle_reaction.py::test_unreact_on_reminder_reverts_to_fired -v
```

Expected: 3 PASSED.

- [ ] **Step 3: Commit**

```bash
cd D:/Kai && git add tests/test_handle_reaction.py && git commit -m "test: add reminder reaction tests (all passing)"
```

---

## Task 4: Task Handling (Add + Remove)

**Files:**
- Modify: `D:/Kai/tests/test_handle_reaction.py` (add tests)

- [ ] **Step 1: Add task tests**

Append to `D:/Kai/tests/test_handle_reaction.py`:

```python
def test_reaction_on_task_nudge_marks_done(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (20, 'outgoing', '📋 [P7] לשלוח דוח שבועי — due in ~6h')")
    conn.execute("INSERT INTO tasks (title, priority, status) VALUES ('לשלוח דוח שבועי', 7, 'open')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_add(message_id=20, reaction='✅', chat_id='123')

    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM tasks WHERE title = 'לשלוח דוח שבועי'").fetchone()[0]
    conn.close()
    assert status == 'done'


def test_reaction_on_overdue_task_marks_done(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (21, 'outgoing', '⚠️ OVERDUE [P8]: לסיים פרויקט X')")
    conn.execute("INSERT INTO tasks (title, priority, status) VALUES ('לסיים פרויקט X', 8, 'open')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_add(message_id=21, reaction='👍', chat_id='123')

    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM tasks WHERE title = 'לסיים פרויקט X'").fetchone()[0]
    conn.close()
    assert status == 'done'


def test_unreact_on_task_reverts_to_open(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text, reactions) VALUES (22, 'outgoing', '📋 [P7] לשלוח דוח שבועי — due in ~6h', '[\"✅\"]')")
    conn.execute("INSERT INTO tasks (title, priority, status) VALUES ('לשלוח דוח שבועי', 7, 'done')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_remove(message_id=22, reaction='✅', chat_id='123')

    conn = sqlite3.connect(db)
    status = conn.execute("SELECT status FROM tasks WHERE title = 'לשלוח דוח שבועי'").fetchone()[0]
    conn.close()
    assert status == 'open'


def test_reaction_on_unrecognized_message_only_logs(tmp_path):
    db = make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (telegram_message_id, direction, text) VALUES (30, 'outgoing', 'שלום! איך אני יכול לעזור?')")
    conn.commit()
    conn.close()

    import handle_reaction
    handle_reaction.DB_PATH = db
    handle_reaction.handle_add(message_id=30, reaction='❤️', chat_id='123')

    conn = sqlite3.connect(db)
    reactions = conn.execute("SELECT reactions FROM messages WHERE telegram_message_id = 30").fetchone()[0]
    conn.close()
    assert json.loads(reactions) == ['❤️']  # logged, no error
```

- [ ] **Step 2: Run task tests**

```bash
cd D:/Kai && python -m pytest tests/test_handle_reaction.py::test_reaction_on_task_nudge_marks_done tests/test_handle_reaction.py::test_reaction_on_overdue_task_marks_done tests/test_handle_reaction.py::test_unreact_on_task_reverts_to_open tests/test_handle_reaction.py::test_reaction_on_unrecognized_message_only_logs -v
```

Expected: 4 PASSED.

- [ ] **Step 3: Run all tests**

```bash
cd D:/Kai && python -m pytest tests/test_handle_reaction.py -v
```

Expected: all PASSED.

- [ ] **Step 4: Commit**

```bash
cd D:/Kai && git add tests/test_handle_reaction.py && git commit -m "test: add task reaction and unrecognized message tests"
```

---

## Task 5: Plugin Patch — Reaction Handler in `server.ts`

**Files:**
- Modify: `D:/Kai/scripts/patch_telegram_plugin.py`

- [ ] **Step 1: Add reaction patch to `patch_telegram_plugin.py`**

Open `D:/Kai/scripts/patch_telegram_plugin.py`. Add the following after the existing constants and before the `patch_file` function:

```python
# --- Reaction handler patch ---
REACTION_IMPORT_MARKER = "{ exec } from 'child_process'"
REACTION_IMPORT_PATCH = "import { exec } from 'child_process';\n"
REACTION_IMPORT_ANCHOR = "import { randomBytes } from 'crypto'"  # stable import near top

REACTION_HANDLER_MARKER = "handle_reaction.py"
REACTION_HANDLER_PATCH = """\
bot.on('message_reaction', async ctx => {
  const chatId = String(ctx.chat?.id ?? ctx.update.message_reaction?.chat?.id ?? '');
  const messageId = String(ctx.update.message_reaction?.message_id ?? '');
  const newReactions = ctx.update.message_reaction?.new_reaction ?? [];
  const oldReactions = ctx.update.message_reaction?.old_reaction ?? [];
  for (const r of newReactions) {
    const emoji = r.type === 'emoji' ? r.emoji : '';
    if (!emoji) continue;
    exec(`python D:/Kai/scripts/handle_reaction.py --chat_id ${chatId} --message_id ${messageId} --reaction "${emoji}" --mode add`,
      (err) => { if (err) console.error('[reaction]', err.message); }
    );
  }
  for (const r of oldReactions) {
    const emoji = r.type === 'emoji' ? r.emoji : '';
    if (!emoji) continue;
    exec(`python D:/Kai/scripts/handle_reaction.py --chat_id ${chatId} --message_id ${messageId} --reaction "${emoji}" --mode remove`,
      (err) => { if (err) console.error('[reaction]', err.message); }
    );
  }
});
"""
REACTION_HANDLER_ANCHOR = "void (async () => {"  # just before the bot.start() retry loop

REACTION_ALLOWED_UPDATES_MARKER = "'message_reaction'"
REACTION_ALLOWED_UPDATES_ANCHOR = "await bot.start({"
REACTION_ALLOWED_UPDATES_PATCH = """\
await bot.start({
        allowed_updates: ['message', 'callback_query', 'message_reaction'],\n"""
```

Then add a new function before `main()`:

```python
def patch_reactions(path: str) -> bool:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    changed = False

    # 1. Add exec import
    if REACTION_IMPORT_MARKER not in content:
        if REACTION_IMPORT_ANCHOR not in content:
            print(f"[patch_telegram] reaction import anchor not found in {path}", file=sys.stderr)
        else:
            content = content.replace(REACTION_IMPORT_ANCHOR, REACTION_IMPORT_PATCH + REACTION_IMPORT_ANCHOR, 1)
            changed = True

    # 2. Add reaction handler
    if REACTION_HANDLER_MARKER not in content:
        if REACTION_HANDLER_ANCHOR not in content:
            print(f"[patch_telegram] reaction handler anchor not found in {path}", file=sys.stderr)
        else:
            content = content.replace(REACTION_HANDLER_ANCHOR, REACTION_HANDLER_PATCH + REACTION_HANDLER_ANCHOR, 1)
            changed = True

    # 3. Add message_reaction to allowed_updates
    if REACTION_ALLOWED_UPDATES_MARKER not in content:
        if REACTION_ALLOWED_UPDATES_ANCHOR not in content:
            print(f"[patch_telegram] allowed_updates anchor not found in {path}", file=sys.stderr)
        else:
            content = content.replace(REACTION_ALLOWED_UPDATES_ANCHOR, REACTION_ALLOWED_UPDATES_PATCH, 1)
            changed = True

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return changed
```

Finally, call `patch_reactions(path)` inside `main()` after `patch_file(path)`:

```python
def main():
    versions = sorted(glob.glob(os.path.join(PLUGIN_CACHE, "*", "server.ts")))
    if not versions:
        print("[patch_telegram] no server.ts found", file=sys.stderr)
        return

    for path in versions:
        applied = patch_file(path)
        verb = "patched" if applied else "already patched"
        print(f"[patch_telegram] reply_to {verb}: {path}", file=sys.stderr)

        reaction_applied = patch_reactions(path)
        verb2 = "patched" if reaction_applied else "already patched"
        print(f"[patch_telegram] reaction {verb2}: {path}", file=sys.stderr)
```

- [ ] **Step 2: Test patch applies correctly**

```bash
python D:/Kai/scripts/patch_telegram_plugin.py 2>&1
```

Expected output: lines like `[patch_telegram] reaction patched: ...server.ts`

- [ ] **Step 3: Verify the patch landed in server.ts**

```bash
grep -n "handle_reaction\|message_reaction\|{ exec }" "C:/Users/Omer/.claude/plugins/cache/claude-plugins-official/telegram/0.0.5/server.ts"
```

Expected: lines containing `handle_reaction.py`, `message_reaction`, and `{ exec } from 'child_process'`.

- [ ] **Step 4: Run patch again — verify idempotency**

```bash
python D:/Kai/scripts/patch_telegram_plugin.py 2>&1
```

Expected: `[patch_telegram] reaction already patched: ...` (no duplicate code).

- [ ] **Step 5: Commit**

```bash
cd D:/Kai && git add scripts/patch_telegram_plugin.py && git commit -m "feat: patch Telegram plugin to handle message_reaction events"
```

---

## Task 6: SessionStart Hook — Re-patch on Restart

The `SessionStart` hook already calls `patch_telegram_plugin.py`. Because our new `patch_reactions()` is added to `main()`, it will run automatically on every session start. No settings.json change needed.

- [ ] **Step 1: Verify the hook is intact**

```bash
python -c "
import json
with open('D:/Kai/.claude/settings.json') as f:
    s = json.load(f)
hooks = s.get('hooks', {}).get('SessionStart', [])
for h in hooks:
    for hook in h.get('hooks', []):
        print(hook.get('command', ''))
"
```

Expected: `python D:/Kai/scripts/patch_telegram_plugin.py` appears in output.

- [ ] **Step 2: Restart Claude Code (or trigger SessionStart manually)**

Run the patch script manually to simulate SessionStart:

```bash
python D:/Kai/scripts/patch_telegram_plugin.py 2>&1
```

Expected: `reaction already patched` for both versions (0.0.4 and 0.0.5).

- [ ] **Step 3: Final commit**

```bash
cd D:/Kai && git add -A && git commit -m "feat: complete Telegram reaction tracking implementation"
```

---

## Manual Verification

After implementation, test end-to-end:

1. Send yourself a reminder: *"תזכיר לי בעוד דקה: בדיקה"*
2. Wait for heartbeat to fire it (⏰ בדיקה)
3. React to that message with any emoji
4. Verify in DB:

```bash
python -c "
import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
conn = sqlite3.connect('D:/Kai/kai.db')
conn.text_factory = str
cur = conn.cursor()
cur.execute('SELECT telegram_message_id, text, reactions FROM messages ORDER BY id DESC LIMIT 5')
for r in cur.fetchall():
    print(r)
cur.execute(\"SELECT id, message, status FROM reminders ORDER BY id DESC LIMIT 5\")
for r in cur.fetchall():
    print(r)
conn.close()
"
```

Expected: the ⏰ message row has `reactions='[\"<your emoji>\"]'` and the reminder `status='dismissed'`.
