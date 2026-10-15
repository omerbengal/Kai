# Kai - a personal AI assistant on top of Claude Code

Kai (קאי) is a personal assistant that has run every day since April 2026. Omer talks to it on
Telegram, in Hebrew or English, by text or voice. It manages tasks and reminders, sends WhatsApp
messages on his behalf, reads and writes his calendar, sends a morning briefing, summarizes TikTok
videos, and investigates and fixes its own bugs when he asks.

There is no app server. Kai is a long-running **Claude Code** session plus:

- agent prompts that turn the session into a dispatcher and its background workers (`agents/`)
- about 90 Python scripts: fast deterministic paths, hooks, watchdogs, scheduled jobs (`scripts/`)
- a SQLite data model for tasks, reminders, contacts and message history (`schema.sql`)
- Claude Code hooks that persist every message and own the typing indicator (`.claude/settings.json`)
- Windows Task Scheduler jobs that keep it alive without a Claude session (heartbeat, morning
  briefing, watchdogs, night mode)

See [ARCHITECTURE.md](ARCHITECTURE.md) for the diagram and [BUILD_STORY.md](BUILD_STORY.md) for how
it got here, including what broke.

## Who built what

Built with AI-assisted development. Omer designed the system, set the requirements, used it daily,
reported every failure and directed every change. Most of the code, prompts and investigation
write-ups were written by Claude Code (Anthropic's agentic coding tool) under his direction, and the
`investigations/` files show that loop: a failure, a root-cause analysis, a fix.

Claude Code itself (the agent runtime, the tool calls, hooks, the Telegram channel plugin, MCP
support) is Anthropic's product and is not part of this repository. Kai is what is built on top of
it: the dispatcher/worker protocol, the scripts, the data model and the operating rules.

## Repository layout

| Path | What it is |
|------|------------|
| `CLAUDE.md` | The project instructions every Kai session loads |
| `agents/listener.md` | The dispatcher: classifies each Telegram message and picks a path (reaction, inline answer, fast script, background worker) |
| `agents/worker.md` | Template for background workers, including the live status-message protocol |
| `agents/principles.md` | About 11 behavioural principles and a 3-question check before every reply |
| `scripts/fast/` | Deterministic single-operation scripts (reminders, tasks, calendar, WhatsApp) that skip the LLM worker |
| `scripts/typing_lease.py` | The typing indicator as a 15-second lease renewed by hooks (see investigation 043) |
| `scripts/patch_telegram_plugin.py` | Patches the Telegram plugin to expose reply-to and reaction events |
| `scripts/run_morning_briefing.py`, `scripts/heartbeat.py` | Standalone scheduled jobs |
| `scripts/local_tz.py`, `scripts/travel_mode.py` | Every clock follows the local timezone, including abroad |
| `scripts/tiktok_summary.py` | TikTok video to Hebrew summary (Supadata transcript, Whisper fallback) |
| `scripts/music_pairs/` | DJ-style song pair suggestions (BPM / Camelot key) |
| `voice/` | Proof of concept: live voice conversation (Gemini Live + local tools) |
| `investigations/` | 8 of the 43 root-cause write-ups (see below) |
| `docs/` | Setup guide, design docs, plans |

## Setup

Kai is a personal system and was never packaged for reuse; paths such as `D:/Kai` are hard-coded.
`docs/kai_setup_guide.md` walks through a full setup. In short:

1. Install Claude Code, Python 3 and Go (for the WhatsApp bridge).
2. Create a Telegram bot with BotFather.
3. Set the environment variables that replace every secret in this public version:

   | Variable | Used for |
   |----------|----------|
   | `KAI_TELEGRAM_BOT_TOKEN` | Telegram Bot API |
   | `KAI_TELEGRAM_CHAT_ID` | The one chat Kai talks to |
   | `GROQ_API_KEY` | Whisper transcription, small LLM calls |
   | `SUPADATA_API_KEY` | TikTok transcripts |
   | `ISRAEL_RAIL_API_KEY` | Train search |

   Google Calendar / Gmail use OAuth files under `credentials/` (client secret + token), created by
   `scripts/fast/fast_cal_auth.py` and `scripts/google_auth_gmail.py`.
4. Create the database: `sqlite3 kai.db < schema.sql` (then `seed.sql`).
5. Get the WhatsApp bridge (below) and start it with `start_whatsapp_bridge.bat`.
6. Start Kai with `start_claude.bat`.

Placeholders you will see in the code and docs: `${KAI_TELEGRAM_BOT_TOKEN}`, `${KAI_TELEGRAM_CHAT_ID}`,
`<REDACTED_...>` (a secret was removed), and `<partner>`, `<contact>`, `<phone>`, `<email>`,
`<jid>`, `<city>` (personal data was removed from examples).

## How this history was assembled

The commit history has three kinds of commits. They are marked so nobody mistakes one kind for
another.

1. **Real commits (26), 10/04/2026-25/04/2026.** The original commits of the private repository,
   with their original messages and dates. Only the files were filtered and scrubbed (see "What was
   removed").
2. **Reconstructed commits (14), 11/09/2026-13/10/2026.** After 25/04 nothing was committed.
   These commits were rebuilt from Claude Code session logs, which record every file Write and Edit
   with a timestamp. Every one of them:
   - starts with `[reconstructed]`,
   - ends with the trailer `Reconstructed-from: Claude Code session log, YYYY-MM-DD`,
   - has the **author date** of the original change and the **committer date** of when it was
     rebuilt (15/10/2026).

   Limits, stated plainly:
   - Session logs only survive from 11/09/2026. The period 26/04-10/09/2026 is a single
     `[reconstructed] Baseline` commit: today's files with every logged edit undone in reverse order.
     Changes made by shell commands after 11/09 could not be separated and are part of that baseline.
   - The logs contain 104 Write/Edit operations on the files published here. 39 edits could not be
     replayed (their `old_string` no longer matched the reconstructed file, usually because a shell
     command had changed it in between), and 46 could not be undone when building the baseline.
     1 file's content before its first logged rewrite is unknown, so it first appears at that rewrite.
   - Intermediate trees are best-effort. Nothing was invented to fill a gap.
3. **Snapshot commit (1), 15/10/2026.** Makes the tree equal to the scrubbed live system, covering
   everything the replay could not. Not reconstructed: it is the real state on that date. The
   documentation commit that follows adds this README, `BUILD_STORY.md` and `ARCHITECTURE.md`.

From 15/10/2026 on, commits are real: a daily job diffs the scrubbed live system against this
repository and commits the changes with real dates, listing the changed files and the reasons
recorded in Kai's action log that day.

## What was removed

The public version goes through one scrubbing script for every commit (real, reconstructed and
future). It is kept outside this repository, because its term lists would themselves leak what they
remove. Every commit, every file version and every commit message was scanned afterwards for bot
tokens, API keys, credential values, phone numbers, emails, WhatsApp IDs, Israeli ID numbers and the
scrubbed names; the scan returned zero hits.

- **Secrets.** Hard-coded tokens and keys were replaced with environment variables. The old values
  live only in the private repository and have been, or should be, rotated.
- **Data.** The SQLite database (messages, contacts), `data/`, logs, temp files, backups, local
  Claude Code state, credentials and the WhatsApp bridge's local store are not in any commit.
- **Personal details.** Names, phone numbers, addresses and private details in prompts, docs, tests
  and comments were replaced with neutral placeholders.
- **Personal workflows.** A few scripts that only served personal-life tasks were left out entirely,
  with their tests and docs.
- **Investigations.** 8 of 43 write-ups are included; the rest quote private conversations too
  closely to scrub reliably.

## Third-party code

- **WhatsApp bridge:** [lharries/whatsapp-mcp](https://github.com/lharries/whatsapp-mcp) by Luke
  Harries, MIT License. Kai uses it as a local bridge (`whatsapp-mcp/`, not included here). Clone it
  next to the scripts and follow its README.
- **Claude Code** and its Telegram channel plugin: Anthropic.
- **Gemini Live** (voice proof of concept): Google API.
- Python dependencies are listed in the scripts that import them (`requests`, `google-api-python-client`,
  `google-auth-oauthlib`, `fastapi`, `pytest`, and others).

## License

Copyright (c) 2026 Omer. All rights reserved. This code is shared for reading only; no license is
granted to use, copy, modify or distribute it. See [LICENSE](LICENSE).
