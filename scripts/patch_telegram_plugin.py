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


# --- Inline keyboard (confirmation buttons) patch ---
INLINE_KB_SCHEMA_MARKER = "inline_keyboard"

# The anchor is the end of the reply tool's format property + required array + opening of react tool
# We match on the unique sequence of lines at the end of reply's properties
_REPLY_SCHEMA_TAIL = (
    "          },\n"
    "        },\n"
    "        required: ['chat_id', 'text'],\n"
    "      },\n"
    "    },\n"
    "    {\n"
    "      name: 'react',"
)

# The format enum property that precedes the tail — used to make the anchor unique
INLINE_KB_SCHEMA_ANCHOR = (
    "            description: \"Rendering mode. 'markdownv2' enables Telegram formatting"
    " (bold, italic, code, links). Caller must escape special chars per MarkdownV2 rules."
    " Default: 'text' (plain, no escaping needed).\",\n"
    "          },\n"
    "        },\n"
    "        required: ['chat_id', 'text'],\n"
    "      },\n"
    "    },\n"
    "    {\n"
    "      name: 'react',"
)

INLINE_KB_SCHEMA_PATCH = (
    "            description: \"Rendering mode. 'markdownv2' enables Telegram formatting"
    " (bold, italic, code, links). Caller must escape special chars per MarkdownV2 rules."
    " Default: 'text' (plain, no escaping needed).\",\n"
    "          },\n"
    "          inline_keyboard: {\n"
    "            type: 'array',\n"
    "            description: 'Optional inline keyboard buttons."
    " Array of rows, each row is array of {text, callback_data}.',\n"
    "            items: {\n"
    "              type: 'array',\n"
    "              items: {\n"
    "                type: 'object',\n"
    "                properties: {\n"
    "                  text: { type: 'string' },\n"
    "                  callback_data: { type: 'string' },\n"
    "                },\n"
    "                required: ['text', 'callback_data'],\n"
    "              },\n"
    "            },\n"
    "          },\n"
    "        },\n"
    "        required: ['chat_id', 'text'],\n"
    "      },\n"
    "    },\n"
    "    {\n"
    "      name: 'react',"
)

INLINE_KB_SEND_MARKER = "inlineKb"
INLINE_KB_SEND_ANCHOR = (
    "            const sent = await bot.api.sendMessage(chat_id, chunks[i], {\n"
    "              ...(shouldReplyTo ? { reply_parameters: { message_id: reply_to } } : {}),\n"
    "              ...(parseMode ? { parse_mode: parseMode } : {}),\n"
    "            })"
)
INLINE_KB_SEND_PATCH = (
    "            const inlineKb = args.inline_keyboard"
    " as Array<Array<{text: string, callback_data: string}>> | undefined\n"
    "            const sent = await bot.api.sendMessage(chat_id, chunks[i], {\n"
    "              ...(shouldReplyTo ? { reply_parameters: { message_id: reply_to } } : {}),\n"
    "              ...(parseMode ? { parse_mode: parseMode } : {}),\n"
    "              ...(inlineKb && i === chunks.length - 1"
    " ? { reply_markup: { inline_keyboard: inlineKb } } : {}),\n"
    "            })"
)

KAI_CONFIRM_MARKER = "Non-perm callback"

# The broken early-return block in the original perm: handler (this is what we replace)
KAI_CONFIRM_ANCHOR = """\
  if (!m) {
    await ctx.answerCallbackQuery().catch(() => {})
    return
  }"""

KAI_CONFIRM_PATCH = """\
  if (!m) {
    // Non-perm callback: Kai confirmation button (yes/no or other callback_data).
    const access = loadAccess()
    const senderId = String(ctx.from.id)
    if (!access.allowFrom.includes(senderId)) {
      await ctx.answerCallbackQuery({ text: 'Not authorized.' }).catch(() => {})
      return
    }
    await ctx.answerCallbackQuery().catch(() => {})
    const cq = ctx.callbackQuery
    const chatId = String(cq.message?.chat.id ?? ctx.from.id)
    const msgId = cq.message?.message_id
    const originalText = (cq.message && 'text' in cq.message ? cq.message.text : '') ?? ''
    const responseText = data === 'yes' ? '\\u2705' : data === 'no' ? '\\u274c' : data
    const callbackText = `[callback: ${responseText}] \\u05e2\\u05dc: "${originalText.substring(0, 100)}"`
    if (msgId != null) {
      await bot.api.editMessageText(chatId, msgId, `${originalText}\\n\\n${responseText}`).catch(() => {})
    }
    mcp.notification({
      method: 'notifications/claude/channel',
      params: {
        content: callbackText,
        meta: {
          chat_id: chatId,
          ...(msgId != null ? { message_id: String(msgId) } : {}),
          user: ctx.from.username ?? String(ctx.from.id),
          user_id: String(ctx.from.id),
          ts: new Date().toISOString(),
          callback_data: data,
          callback_original_text: originalText.substring(0, 300),
        },
      },
    }).catch(err => {
      process.stderr.write(`telegram channel: failed to deliver callback to Claude: ${err}\\n`)
    })
    return
  }"""


def patch_inline_keyboard(path: str) -> bool:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    changed = False

    # 1. Add inline_keyboard to reply tool schema
    if INLINE_KB_SCHEMA_MARKER not in content:
        if INLINE_KB_SCHEMA_ANCHOR not in content:
            print(f"[patch_telegram] inline_kb schema anchor not found in {path}", file=sys.stderr)
        else:
            content = content.replace(INLINE_KB_SCHEMA_ANCHOR, INLINE_KB_SCHEMA_PATCH, 1)
            changed = True

    # 2. Add reply_markup injection in sendMessage
    if INLINE_KB_SEND_MARKER not in content:
        if INLINE_KB_SEND_ANCHOR not in content:
            print(f"[patch_telegram] inline_kb send anchor not found in {path}", file=sys.stderr)
        else:
            content = content.replace(INLINE_KB_SEND_ANCHOR, INLINE_KB_SEND_PATCH, 1)
            changed = True

    # 3. Patch the perm: handler's early-return to include Kai confirm logic (single merged handler)
    if KAI_CONFIRM_MARKER not in content:
        if KAI_CONFIRM_ANCHOR not in content:
            print(f"[patch_telegram] kai_confirm anchor not found in {path}", file=sys.stderr)
        else:
            content = content.replace(KAI_CONFIRM_ANCHOR, KAI_CONFIRM_PATCH, 1)
            changed = True

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return changed


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



def remove_duplicate_callback_handler(path: str) -> bool:
    """Remove the old duplicate bot.on('callback_query:data') handler if present.
    The old patch added a second handler which grammY never reached due to early return in first.
    The new approach merges the logic into the single first handler."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    first_idx = text.find("bot.on('callback_query:data'")
    second_idx = text.find("bot.on('callback_query:data'", first_idx + 1)
    if second_idx < 0:
        return False  # no duplicate, nothing to do

    comment_marker = "// Kai inline-keyboard confirmation handler (kai_confirm)."
    comment_idx = text.rfind(comment_marker, 0, second_idx)
    if comment_idx >= 0:
        nl_idx = text.rfind("\n", 0, comment_idx)
        removal_start = nl_idx + 1
    else:
        removal_start = second_idx

    anchor = "bot.on('message_reaction', async ctx => {"
    anchor_idx = text.find(anchor, second_idx)
    if anchor_idx < 0:
        print(f"[patch_telegram] cleanup: message_reaction anchor not found, skipping {path}", file=sys.stderr)
        return False

    patched = text[:removal_start] + text[anchor_idx:]
    with open(path, "w", encoding="utf-8") as f:
        f.write(patched)
    return True


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

    versions = sorted(glob.glob(os.path.join(PLUGIN_CACHE, "*", "server.ts")))
    if not versions:
        print("[patch_telegram] no server.ts found", file=sys.stderr)
        return

    # patch all cached versions (the CLI may run any of them)
    for path in versions:
        applied = patch_file(path)
        verb = "patched" if applied else "already patched"
        print(f"[patch_telegram] reply_to {verb}: {path}", file=sys.stderr)

        # Clean up old duplicate handler if present (from legacy kai_confirm patch approach)
        removed_dup = remove_duplicate_callback_handler(path)
        if removed_dup:
            print(f"[patch_telegram] removed duplicate callback handler: {path}", file=sys.stderr)

        reaction_applied = patch_reactions(path)
        verb2 = "patched" if reaction_applied else "already patched"
        print(f"[patch_telegram] reaction {verb2}: {path}", file=sys.stderr)

        inline_kb_applied = patch_inline_keyboard(path)
        verb3 = "patched" if inline_kb_applied else "already patched"
        print(f"[patch_telegram] inline_keyboard {verb3}: {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
