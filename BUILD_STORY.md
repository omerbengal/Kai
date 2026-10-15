# Build story

How Kai went from a design doc to a system that runs every day: what was built, what broke and
what it taught. Dates are 2026. Sources: the git history, the `investigations/` write-ups (43 of
them; 8 are included in this repository), the design docs and the Claude Code session logs.

## April: from "Nova" to Kai

- **10/04 - design first.** The project started as "Nova": a design spec and an implementation
  plan, then the SQLite schema, and three agent prompts (listener, heartbeat, morning briefing)
  with Claude Code cron jobs. The same day two external dependencies were dropped: Brave Search was
  replaced by SearXNG over `curl`, and the SQLite MCP server by plain Python `sqlite3`.
- **11/04 - reactions as input.** A spec, a plan and test-first implementation let a Telegram
  reaction act on what it reacts to: 👍 on a reminder marks it done, removing the reaction undoes
  it. The Telegram plugin did not expose reactions, so a patch script was written for it.
- **12/04-25/04 - the dispatcher/worker split.** The main session became a dispatcher that
  classifies each message and delegates; background workers do the work in parallel and edit a
  single live status message. Voice messages got Whisper transcription.
  Simple operations got "fast scripts" that skip the LLM worker.
- The first 18 investigations come from this period: a transcription status message that was not
  deleted on some paths (001, 003), a voice message stuck behind an auto-compaction (002), the
  typing indicator stopping after 4-6 seconds (011, 013), a leaked PID file keeping "typing..."
  alive (015, 016), and the "personal assistant voice" principle (018): when Omer asks Kai to send a
  message, he describes the intent and Kai writes it the way he would.
- **25/04** - the last real commit: the whole infrastructure consolidated. After this nothing was
  committed until the public version.

## May: gaps in the fast path, reminders, TikTok

- Fast scripts could not handle all-day events (017) or echo a voice transcription (019), and they
  bypassed the hook that saves outgoing messages, so reactions to their messages did nothing (020).
  Each gap pushed simple requests back onto slow workers.
- A WhatsApp message got Kai's signature twice because two layers added it (023); the send layer now
  owns it.
- The transcript stage of the TikTok summarizer failed on every video, so every run fell back to
  the slow browser + Whisper path (024). The script called an endpoint that does not exist in the
  transcript service's API.
- Reminders nag every 5 minutes until acknowledged. A reaction did not stop them (025); the first
  fix patched the wrong copy of the plugin (026); and reactions never woke Claude at all, because
  the plugin emitted no channel event for them (027).
- The morning briefing arrived at 09:32 instead of 09:00 (028): the briefing agent worked as
  "write a script, run it, read the output, write the next one", so a 1-3 minute job took about 32
  minutes. It also once missed a holiday eve (029). The briefing later moved out of Claude
  entirely, into a standalone script run by Windows Task Scheduler.
- **30/05** - music pairs v2: song-pair suggestions for DJ sets with BPM and harmonic-key matching,
  sourced from real DJ sets (030).

## June-July: the session that would not hear

- Hebrew right-to-left writing got its own rules: status emoji at the end of the line, arrows that
  point left (031).
- After every restart Kai stopped receiving Telegram messages until someone typed
  `/reload-plugins` by hand. Five investigations in one day (033-037) chased the wrong causes:
  a patch that kept reverting, an orphaned poller stealing updates, a poller that gave up on HTTP
  409. Investigation 038 found the real one: Claude Code connects the channel plugin lazily,
  minutes after the session starts. The fix is a `/reload-plugins` initial prompt at startup.
- A summary Omer asked Kai to *prepare* for a group was *posted* to the group (039). Since then
  "create / prepare for the group" means a draft for Omer, and only "send" sends.

## August: staying alive

- **Night mode**: Kai and the WhatsApp bridge shut down at 02:00 and start at 05:00 local time.
- The automatic restart had silently never worked for three weeks: the watchdog's restart was
  killed together with the session it restarted (a Windows job object). It was replaced by an
  on-demand scheduled restart task with its own log.
- The nightly restart deleted a real reminder message instead of its own status message (040);
  recurring reminders were sent once a day instead of nagging every 5 minutes until acknowledged
  (041); dead terminal windows piled up after every restart
  (042).

## September: ownership and principles

- **09/09 - the typing indicator, for the last time** (043). Ten complaints and six spot fixes over
  five months had one cause: nobody owned the indicator, several mechanisms started it and one tried
  to stop it. It became a single 15-second lease renewed by tool-call hooks, and agents are forbidden
  to touch it.
- **13/09 - travel mode**: every clock (reminders, briefing, night mode) follows the local timezone
  while abroad, through one module, `local_tz.py`.
- **24-26/09 - live voice proof of concept**: a web page with Gemini Live for speech and fast local
  tools, handing heavy requests to Claude. Anything it would send to another person becomes a draft
  that needs a tap on Telegram. Paused afterwards; speed was the hard requirement.
- **28/09 - principles**: dozens of narrow rules, each from one incident, were folded into about 11
  principles and a 3-question check before every reply. Status messages became silent and are pinned
  only when a background worker is actually running.

## October

- **10/10** - a rule the TikTok prompt already had ("one rhetorical device per post") failed in
  practice, so it moved into code: repeated devices are detected and rewritten.
- **11/10 - step shortening**: every tool call is a full model turn (about 6 seconds), so the
  dispatcher's three opening calls became one. Greetings and "are you there?" are answered at once.
- **15/10** - this public version.

## What it taught

- **Own each piece of infrastructure in one place.** The typing indicator, restarts and the plugin
  patch all broke when several actors managed them.
- **A rule that matters belongs in code.** Prompt-only rules failed twice in the same way before
  they were enforced by a script.
- **Verify before saying "done".** Most trust lost came from reporting success that was not checked
  on Omer's side.
- **The lightest path wins.** A worker for a one-line job is a 30-second wait; the fast-script and
  inline paths exist because Omer noticed every second.
- **Investigate before fixing.** Five wrong root causes for the deaf channel were each plausible;
  only a controlled experiment found the real one.
- **Outward actions are irreversible.** Anything that reaches other people gets a confirmation
  step whenever the wording, recipient or intent is ambiguous.

## How the commit history was assembled

The commit history has three kinds of commits. They are marked so nobody mistakes one kind for
another.

1. **Real commits (26), 10/04/2026-25/04/2026.** The original commits of the private repository,
   with their original messages and dates. Only the files were filtered and scrubbed.
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
   everything the replay could not. Not reconstructed: it is the real state on that date.

From 15/10/2026 on, commits are real: each one is made at the end of a finished feature or fix.
