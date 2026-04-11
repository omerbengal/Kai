# Telegram Reaction Tracking — Design Spec

**Date:** 2026-04-11  
**Status:** Approved

---

## Overview

When Omer reacts to a Telegram message with any emoji, Kai should:
1. Log the reaction in the `messages` table
2. If the message was a reminder or task notification, mark it as handled

---

## Behavior

| Message type | Reaction effect |
|---|---|
| ⏰ one-shot reminder | Log reaction + set `reminders.status = 'dismissed'` |
| ⏰ recurring reminder | Log reaction only (reminder keeps cycling) |
| 📋 / ⚠️ task nudge | Log reaction + set `tasks.status = 'done'` |
| Any other message | Log reaction only |

**On success:** silent (no Telegram reply).  
**On error:** send a Telegram error message to chat_id ${KAI_TELEGRAM_CHAT_ID}.

---

## DB Changes

### `messages` table
Add one column:

```sql
ALTER TABLE messages ADD COLUMN reactions TEXT;
-- stores a JSON array of emoji strings, e.g. '["👍"]' or '["👍","❤️"]'
-- NULL when no reactions recorded
```

No changes to `tasks` or `reminders` schema — existing statuses suffice:
- `reminders.status`: `pending` → (heartbeat) → `fired` (one-shot) or stays `pending` (recurring) → (user reacts, one-shot only) → `dismissed`
- `tasks.status`: `open` → (user reacts) → `done`

**Status semantics:**
- `fired` = Kai sent the notification; user has not yet explicitly acknowledged
- `dismissed` = user actively reacted and acknowledged/handled the reminder

---

## New Script: `scripts/handle_reaction.py`

**Invocation:** called by the Telegram plugin via `child_process.exec`

```
python D:/Kai/scripts/handle_reaction.py \
  --chat_id <chat_id> \
  --message_id <telegram_message_id> \
  --reaction <emoji> \
  --mode <add|remove>
```

`--mode add` (default) = user added a reaction. `--mode remove` = user removed a reaction.

**Algorithm (add):**

1. Look up `messages` by `telegram_message_id = message_id`
   - If not found: log to stderr, send Telegram error, exit
2. Append `reaction` to `messages.reactions` JSON array (create if NULL)
3. Parse message text to determine type:
   - Starts with `⏰` → reminder
   - Starts with `📋` or `⚠️` → task nudge
   - Otherwise → done (reaction logged, nothing further)
4. **Reminder path:**
   - Extract reminder message text (strip `⏰ ` prefix)
   - Look up `reminders WHERE message = <text>`
   - If `recurrence IS NULL` (one-shot): set `status = 'dismissed'`
   - If recurring: no status change
5. **Task path:**
   - For `📋` messages: format is `📋 [P{n}] {title} — due in ~{X}h` → strip prefix `📋 [P{n}] ` and suffix ` — due in ~{X}h`
   - For `⚠️` messages: format is `⚠️ OVERDUE [P{n}]: {title}` → strip prefix `⚠️ OVERDUE [P{n}]: `
   - Look up `tasks WHERE title = <extracted_title> AND status = 'open'`
   - Set `status = 'done'`
6. Log to `action_log`
7. On any exception: send Telegram error message, exit 1

**Algorithm (remove):**

1. Look up `messages` by `telegram_message_id = message_id`
2. Remove `reaction` from `messages.reactions` JSON array
3. Reverse the original action:
   - `⏰` one-shot reminder with `status = 'dismissed'` → set back to `fired`
   - `📋`/`⚠️` task with `status = 'done'` → set back to `open`
   - Otherwise → nothing further
4. Log to `action_log`

**Error handling:** all exceptions caught at top level, reported via Telegram, never crash silently.

---

## Plugin Patch: `patch_telegram_plugin.py`

Add a second patch function alongside the existing `reply_to` patch. The new patch inserts two things into `server.ts`:

### 1. Import `exec` at the top of server.ts

Anchor: first line with `import`

```ts
import { exec } from 'child_process';
```

### 2. `message_reaction` handler

Anchor: just before the `bot.start(` call

```ts
bot.on('message_reaction', async ctx => {
  const chatId = String(ctx.chat?.id ?? ctx.update.message_reaction?.chat?.id ?? '');
  const messageId = String(ctx.update.message_reaction?.message_id ?? '');
  const newReactions = ctx.update.message_reaction?.new_reaction ?? [];
  const oldReactions = ctx.update.message_reaction?.old_reaction ?? [];
  // Added reactions
  for (const r of newReactions) {
    const emoji = r.type === 'emoji' ? r.emoji : '';
    if (!emoji) continue;
    exec(`python D:/Kai/scripts/handle_reaction.py --chat_id ${chatId} --message_id ${messageId} --reaction "${emoji}" --mode add`,
      (err) => { if (err) console.error('[reaction]', err.message); }
    );
  }
  // Removed reactions
  for (const r of oldReactions) {
    const emoji = r.type === 'emoji' ? r.emoji : '';
    if (!emoji) continue;
    exec(`python D:/Kai/scripts/handle_reaction.py --chat_id ${chatId} --message_id ${messageId} --reaction "${emoji}" --mode remove`,
      (err) => { if (err) console.error('[reaction]', err.message); }
    );
  }
});
```

### 3. Add `message_reaction` to `allowed_updates`

The `bot.start()` call needs `allowed_updates` to include `message_reaction`. Find the `bot.start(` call and patch in the option if not already present.

**Idempotency:** each patch checks for a unique marker string before applying, same as the existing `reply_to` patch. All patches re-apply on each `SessionStart` hook (safe to run multiple times).

---

## Data Flow

```
Omer reacts on Telegram
  → Telegram Bot API sends message_reaction update
  → grammy delivers to bot.on('message_reaction', ...)
  → server.ts calls child_process.exec(handle_reaction.py)
  → handle_reaction.py:
      1. Updates messages.reactions
      2. Marks reminder dismissed / task done (if applicable)
      3. Logs to action_log
  → Claude Code never woken up
```

---

## Files Changed

| File | Change |
|---|---|
| `D:/Kai/kai.db` | `ALTER TABLE messages ADD COLUMN reactions TEXT` |
| `D:/Kai/scripts/handle_reaction.py` | New script |
| `D:/Kai/scripts/patch_telegram_plugin.py` | Add second patch for reaction handler |
| `C:/Users/Omer/.claude/plugins/cache/claude-plugins-official/telegram/*/server.ts` | Patched at SessionStart |

---

## Un-reacting (Removing a Reaction)

When `old_reaction` is non-empty and `new_reaction` is empty, the user removed a reaction.

**Behavior:** reverse the original action:
- Task that was marked `done` → revert to `open`
- Reminder that was marked `dismissed` → revert to `fired`
- Remove the emoji from `messages.reactions`

The script checks `--mode` arg: `add` (default) or `remove`. On removal, it looks up the emoji in `messages.reactions` and removes it, then reverses the status change.

## Notes

- Reactions on Omer's own messages (direction='incoming') are logged like any other unrecognized message — no task/reminder action, just the emoji recorded in `messages.reactions`.
- Multiple emoji reactions on the same message: each handled independently.
