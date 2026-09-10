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

PLUGIN_PATHS = [
    r"C:\Users\Omer\.claude\plugins\marketplaces\claude-plugins-official\external_plugins\telegram",
    r"C:\Users\Omer\.claude\plugins\cache\claude-plugins-official\telegram",
]


def find_server_files():
    found = []
    for base in PLUGIN_PATHS:
        direct = os.path.join(base, "server.ts")
        if os.path.exists(direct):
            found.append(direct)
        found.extend(sorted(glob.glob(os.path.join(base, "*", "server.ts"))))
    return found

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

# v2 marker — unique to the new handler so the v1 handler from #026 is detected
# as "needs upgrade" and force-replaced by patch_reactions().
REACTION_HANDLER_MARKER_V2 = "event_type: 'reaction'"

REACTION_HANDLER_PATCH = """\
bot.on('message_reaction', async ctx => {
  const upd = ctx.update.message_reaction;
  const chatId = String(ctx.chat?.id ?? upd?.chat?.id ?? '');
  const messageId = String(upd?.message_id ?? '');
  const userObj = upd?.user ?? ctx.from;
  const userId = String(userObj?.id ?? '');
  const username = userObj?.username ?? userId;

  try { assertAllowedChat(chatId); } catch { return; }

  const newReactions = upd?.new_reaction ?? [];
  const oldReactions = upd?.old_reaction ?? [];
  const newEmojis = newReactions.filter(r => r.type === 'emoji').map(r => (r as any).emoji as string);
  const oldEmojis = oldReactions.filter(r => r.type === 'emoji').map(r => (r as any).emoji as string);

  for (const emoji of newEmojis) {
    exec(`python D:/Kai/scripts/handle_reaction.py --chat_id ${chatId} --message_id ${messageId} --reaction "${emoji}" --mode add`,
      (err) => { if (err) console.error('[reaction]', err.message); }
    );
  }
  for (const emoji of oldEmojis) {
    exec(`python D:/Kai/scripts/handle_reaction.py --chat_id ${chatId} --message_id ${messageId} --reaction "${emoji}" --mode remove`,
      (err) => { if (err) console.error('[reaction]', err.message); }
    );
  }

  if (newEmojis.length === 0 && oldEmojis.length === 0) return;
  const added = newEmojis.join('');
  const removed = oldEmojis.join('');
  const parts: string[] = [];
  if (added)   parts.push(`added: ${added}`);
  if (removed) parts.push(`removed: ${removed}`);
  const content = `[reaction on message ${messageId}] ${parts.join(', ')}`;

  mcp.notification({
    method: 'notifications/claude/channel',
    params: {
      content,
      meta: {
        chat_id: chatId,
        message_id: messageId,
        user: username,
        user_id: userId,
        ts: new Date((upd?.date ?? 0) * 1000).toISOString(),
        event_type: 'reaction',
        reactions_added: added,
        reactions_removed: removed,
      },
    },
  }).catch(err => {
    process.stderr.write(`telegram channel: failed to deliver reaction event to Claude: ${err}\n`)
  });
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


# --- Poll give-up on 409 patch (investigation #035) ---
# The poll loop gave up permanently after 8 consecutive 409 Conflicts (~28s of
# cumulative backoff). But a force-killed old poller keeps Telegram's getUpdates
# slot held for up to ~30s, so the new poller quit ~2s BEFORE the slot freed and
# went permanently deaf (process alive, MCP stdin keeps it up) until a manual
# /reload-plugins. Fix: never give up on 409 — keep retrying (15s cap) so the
# poller recovers the instant Telegram frees the slot.
POLL_GIVEUP_MARKER = "fix035"
POLL_GIVEUP_ANCHOR = "      if (is409 && attempt >= 8) {"
POLL_GIVEUP_PATCH = (
    "      if (false) { // fix035 (#035): never give up on 409 - a force-killed old "
    "poller holds Telegram's getUpdates slot ~30s; retry indefinitely (15s cap) so the "
    "poller recovers the instant it frees instead of going permanently deaf until reload-plugins."
)


def patch_poll_giveup(path: str) -> bool:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if POLL_GIVEUP_MARKER in content:
        return False  # already patched
    if POLL_GIVEUP_ANCHOR not in content:
        print(f"[patch_telegram] poll_giveup anchor not found in {path}", file=sys.stderr)
        return False
    content = content.replace(POLL_GIVEUP_ANCHOR, POLL_GIVEUP_PATCH, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return True


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

    # 2. Add (or upgrade) reaction handler.
    # Force re-patch: if old v1 handler exists but v2 marker doesn't,
    # surgically remove the old handler block before inserting v2.
    if "handle_reaction.py" in content and REACTION_HANDLER_MARKER_V2 not in content:
        import re
        # Match old v1 handler: opens with `bot.on('message_reaction', async ctx => {`
        # and closes at the matching `});` (the v1 handler had no nested braces requiring
        # a real parser). DOTALL + non-greedy stops at the FIRST closing `});` line.
        old_pattern = re.compile(
            r"bot\.on\('message_reaction',\s*async\s+ctx\s*=>\s*\{.*?^\}\);\s*\n",
            re.DOTALL | re.MULTILINE
        )
        new_content, n = old_pattern.subn("", content, count=1)
        if n == 1:
            content = new_content
            changed = True

    if REACTION_HANDLER_MARKER_V2 not in content:
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


def _server_is_patched(server_path: str) -> bool:
    """True only if the given server.ts has ALL Kai patches applied.
    Used to decide whether a running plugin process is healthy or broken."""
    try:
        with open(server_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return False
    return (
        MARKER in content                       # reply_to
        and REACTION_HANDLER_MARKER_V2 in content  # reaction v2
        and INLINE_KB_SCHEMA_MARKER in content      # inline keyboard
        and KAI_CONFIRM_MARKER in content           # kai_confirm callback
    )


def _cwd_from_cmdline(cmdline: str):
    """Extract the plugin dir from a bun/node CommandLine ('... --cwd <dir> ...')."""
    import shlex
    try:
        parts = shlex.split(cmdline, posix=False)
    except Exception:
        parts = cmdline.split()
    for i, p in enumerate(parts):
        if p == "--cwd" and i + 1 < len(parts):
            return parts[i + 1].strip('"')
        if p.startswith("--cwd="):
            return p.split("=", 1)[1].strip('"')
    return None


def kill_stray_plugin_processes(session_start: bool = False):
    """Kill stale/broken telegram plugin bun/node processes.

    IMPORTANT (investigation #033): killing a telegram bun process MID-SESSION is
    destructive — nothing respawns it, so the channel goes dead until a manual
    /reload-plugins. Therefore killing is gated behind `session_start`: only the
    SessionStart hook (which is followed by Claude Code spawning a fresh bun) may
    kill. Watchdog / manual invocations pass session_start=False and NEVER kill —
    they only re-patch the on-disk files (safe for the running process).

    At SessionStart we kill a telegram process when it is EITHER:
      • serving unpatched code (already broken — missing a Kai patch), OR
      • an ORPHAN — its parent claude.exe is no longer alive (a leftover poller
        from a prior/dead session).

    Investigation #034: the old criterion was "age > 60s", a fragile proxy for
    "orphan" that could kill the CURRENT session's own poller (or leave nothing
    polling if Claude had not yet respawned one). We now key on parent liveness
    directly: the current session's bun has a LIVE parent claude.exe → never
    killed, regardless of age. Only genuine orphans (dead parent) — which steal
    Telegram getUpdates and get nothing delivered — are removed."""
    if not session_start:
        return
    import subprocess, json as _json
    try:
        # Live claude.exe PIDs — a telegram bun whose parent is in this set is the
        # current session's poller and must never be killed.
        claude_res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-Process -Name claude -ErrorAction SilentlyContinue | "
             "Select-Object -ExpandProperty Id) -join ','"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        live_claude = {int(p) for p in (claude_res.stdout or "").strip().split(",") if p.strip().isdigit()}

        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                (
                    "Get-CimInstance Win32_Process -Filter \"Name='bun.exe' OR Name='node.exe'\" | "
                    "Where-Object { $_.CommandLine -like '*claude-plugins-official*telegram*' } | "
                    "ForEach-Object { [pscustomobject]@{ pid=$_.ProcessId; ppid=$_.ParentProcessId; "
                    "age=((Get-Date) - $_.CreationDate).TotalSeconds; cmd=$_.CommandLine } } | "
                    "ConvertTo-Json -Compress"
                ),
            ],
            capture_output=True, text=True, timeout=15, check=False,
        )
        out = (result.stdout or "").strip()
        if not out:
            return
        data = _json.loads(out)
        if isinstance(data, dict):
            data = [data]

        for proc in data:
            pid = proc.get("pid")
            ppid = proc.get("ppid")
            age = proc.get("age", 0) or 0
            cmdline = proc.get("cmd") or ""
            cwd = _cwd_from_cmdline(cmdline)
            server_path = os.path.join(cwd, "server.ts") if cwd else None
            is_patched = bool(server_path and os.path.exists(server_path)
                              and _server_is_patched(server_path))
            parent_alive = ppid in live_claude

            # Preserve the current session's healthy poller (live claude parent + patched).
            if is_patched and parent_alive:
                print(f"[patch_telegram] keeping session poller PID {pid} "
                      f"(ppid={ppid} alive, age={age:.0f}s)", file=sys.stderr)
                continue

            if not is_patched:
                reason = "unpatched"
            elif not parent_alive:
                reason = f"orphan (dead parent ppid={ppid})"
            else:
                reason = "unknown"
            print(f"[patch_telegram] killing {reason} plugin process PID {pid}", file=sys.stderr)
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
                capture_output=True, timeout=10, check=False,
            )
    except Exception as e:
        print(f"[patch_telegram] kill_stray failed (non-fatal): {e}", file=sys.stderr)


def _set_readonly(path: str, readonly: bool):
    """Toggle the read-only bit on a file (Windows-friendly, no-op on failure).

    Fix #033: locking patched server.ts read-only prevents a plugin auto-update
    from silently overwriting it and reverting the Kai patch. Our own patch pass
    always clears the bit before writing, so re-patching still works."""
    import stat
    try:
        os.chmod(path, stat.S_IREAD if readonly else stat.S_IWRITE)
    except Exception as e:
        print(f"[patch_telegram] chmod {'+R' if readonly else '-R'} failed for {path}: {e}",
              file=sys.stderr)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

    session_start = "--session-start" in sys.argv
    # A headless `claude -p` child (a batch scraper runs one per batch of
    # posts) fires SessionStart like any other session, so it arrived here with
    # --session-start and was allowed to kill plugin processes. That child has
    # no Telegram channel of its own and nothing to patch for; letting it into
    # the destructive path put the live session's poller at risk a dozen times
    # per scrape. Demote it to a plain, non-killing invocation.
    if session_start and os.environ.get("KAI_HEADLESS_CHILD"):
        session_start = False
    lock = "--no-lock" not in sys.argv  # read-only lock on by default

    # --- Timing instrumentation (investigation #036) ---
    # This hook is now async (settings.json), so it no longer blocks Claude Code's
    # startup / channel-plugin connection. We still time it to confirm on the next
    # controlled restart that (a) the hook is fast and (b) the boot->channel-connect
    # gap in the MCP log collapses. Correlate this log's timestamps with
    # %LOCALAPPDATA%\claude-cli-nodejs\Cache\D--Kai\mcp-logs-plugin-telegram-telegram.
    import time as _time, datetime as _dt
    _t0 = _time.time()

    def _tlog(msg):
        try:
            with open("D:/Kai/tmp/patch_hook_timing.log", "a", encoding="utf-8") as _f:
                _f.write(f"{_dt.datetime.now().isoformat(timespec='milliseconds')} "
                         f"+{_time.time() - _t0:5.2f}s  {msg}\n")
        except Exception:
            pass

    _tlog(f"=== hook start (session_start={session_start}, async) ===")

    # Killing is destructive and only safe at SessionStart (see kill_stray docstring).
    kill_stray_plugin_processes(session_start=session_start)
    _tlog("kill_stray done")

    versions = find_server_files()
    if not versions:
        msg = f"[patch_telegram] CRITICAL: no server.ts found in any of: {PLUGIN_PATHS}"
        print(msg, file=sys.stderr)
        try:
            import urllib.request, urllib.parse
            urllib.request.urlopen(
                f'https://api.telegram.org/bot{os.environ["KAI_TELEGRAM_BOT_TOKEN"]}/sendMessage?'
                f'chat_id={os.environ["KAI_TELEGRAM_CHAT_ID"]}&text={urllib.parse.quote("⚠️ patch_telegram_plugin: לא נמצא server.ts באף אחד מהנתיבים. הריאקציות לא יעבדו.")}',
                timeout=5)
        except Exception:
            pass
        return

    # patch all cached versions (the CLI may run any of them)
    for path in versions:
        # Clear the read-only lock so our own patch writes succeed.
        _set_readonly(path, False)

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

        poll_giveup_applied = patch_poll_giveup(path)
        verb4 = "patched" if poll_giveup_applied else "already patched"
        print(f"[patch_telegram] poll_giveup {verb4}: {path}", file=sys.stderr)

        # Re-apply the read-only lock so plugin auto-updates can't revert the patch.
        # Only lock files that ended up fully patched (don't lock a file whose anchors
        # weren't found — that would freeze it broken).
        if lock and _server_is_patched(path):
            _set_readonly(path, True)
            print(f"[patch_telegram] locked read-only: {path}", file=sys.stderr)

    try:
        import subprocess
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='bun.exe'\" | "
             "Where-Object { $_.CommandLine -like '*--cwd*telegram*' } | "
             "Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, timeout=10)
        if result.stdout:
            for path in versions:
                plugin_dir = os.path.dirname(path)
                if plugin_dir.lower() not in result.stdout.lower():
                    print(f"[patch_telegram] WARN: patched {path} but no bun process is running it",
                          file=sys.stderr)
    except Exception:
        pass

    _tlog("=== hook end ===")


if __name__ == "__main__":
    main()
