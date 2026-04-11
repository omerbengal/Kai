#!/usr/bin/env python3
"""
Ensures the Telegram plugin's server.ts includes reply_to_message_id in the
notification meta. Re-applies the patch after plugin auto-updates.

Called from the SessionStart hook before MCP servers start.
"""

import sys
import io
import glob
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

PLUGIN_CACHE = r"C:\Users\Omer\.claude\plugins\cache\claude-plugins-official\telegram"

MARKER = "reply_to_message_id"

PATCH = """\
        ...(ctx.message?.reply_to_message ? {
          reply_to_message_id: String(ctx.message.reply_to_message.message_id),
          reply_to_message_text: (ctx.message.reply_to_message.text ?? ctx.message.reply_to_message.caption ?? '').slice(0, 300),
        } : {}),\n"""

ANCHOR = "        ...(attachment ? {"


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

REACTION_ALLOWED_UPDATES_MARKER = "'message', 'callback_query', 'message_reaction'"
REACTION_ALLOWED_UPDATES_ANCHOR = "await bot.start({"
REACTION_ALLOWED_UPDATES_PATCH = """\
await bot.start({
        allowed_updates: ['message', 'callback_query', 'message_reaction'],\n"""


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


def patch_file(path: str) -> bool:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if MARKER in content:
        return False  # already patched

    if ANCHOR not in content:
        print(f"[patch_telegram] anchor not found in {path}, skipping", file=sys.stderr)
        return False

    patched = content.replace(ANCHOR, PATCH + ANCHOR, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(patched)
    return True


def main():
    versions = sorted(glob.glob(os.path.join(PLUGIN_CACHE, "*", "server.ts")))
    if not versions:
        print("[patch_telegram] no server.ts found", file=sys.stderr)
        return

    # patch all cached versions (the CLI may run any of them)
    for path in versions:
        applied = patch_file(path)
        verb = "patched" if applied else "already patched"
        print(f"[patch_telegram] reply_to {verb}: {path}", file=sys.stderr)

        reaction_applied = patch_reactions(path)
        verb2 = "patched" if reaction_applied else "already patched"
        print(f"[patch_telegram] reaction {verb2}: {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
