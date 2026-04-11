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
        print(f"[patch_telegram] {verb}: {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
