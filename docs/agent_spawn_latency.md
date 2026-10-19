# Agent spawn latency - measurement and options (19/10/2026)

Omer: "לייצר אייג'נט לוקח לך בערך דקה-דקה וחצי". Research only - nothing was changed.

## 1. Measurement

Sources: `messages.ts` (Telegram time of Omer's message), `ack_log` (written by `scripts/log_ack.py`),
the main-session transcript (`~/.claude/projects/D--Kai/f5e6736c-....jsonl`, timestamp of every tool call)
and each worker's transcript (`.../subagents/agent-*.jsonl`).

### Last 7 days (43 worker spawns, message -> worker's first "agent מוכן" edit)

| | median | p75 | max |
|---|---|---|---|
| message -> dispatcher ACK logged | 21 s | 25 s | 75 s |
| ACK logged -> worker "agent מוכן" | 27 s | 34 s | 64 s |
| **total** | **47 s** | **61 s** | **108 s** |

After "agent מוכן" the worker still reads principles.md etc. before real work, so Omer's "about a minute" is accurate.

### Today, per step (seconds, from transcripts)

| step | 17261 | 17265 | 17312 | 17316 | 17345 | avg | share |
|---|---|---|---|---|---|---|---|
| Telegram -> session gets the prompt | 1 | 1 | 0 | 0 | 0 | ~0.5 | 1% |
| dispatcher first turn (TTFT, 320k cached context) | 2.4 | 3.7 | 4.2 | 17.2 | 4.4 | 6.4 | 12% |
| `dispatch_start.py` (+ listener.md Read in 17261) | 11.2 | 2.0 | 1.0 | 11.1 | 11.0 | 7.3 | 13% |
| turn: edit ACK + `log_ack.py` (incl. worker.md Read in 17261) | 5.4 | 3.6 | 2.0 | 7.1 | 1.9 | 4.0 | 7% |
| `log_ack.py` wall time (trivial insert) | 7.8 | 2.3 | 9.6 | 7.3 | 1.0 | 5.6 | 10% |
| **dispatcher writes the worker prompt (Agent call)** | **19.2** | **28.9** | **20.4** | **31.9** | **22.6** | **24.6** | **46%** |
| output tokens of that turn | 2223 | 3294 | 2143 | 3506 | 2438 | 2720 | |
| worker cold start -> first edit | 5.1 | 4.4 | 5.2 | 6.3 | 5.0 | 5.2 | 10% |
| **total to "agent מוכן"** | 52 | 46 | 43 | 81 | 47 | **54** | |

Reference: 17304 was a follow-up via `SendMessage` to an existing agent (no new prompt): **24 s**.

### Findings

1. **The bottleneck is the dispatcher writing a 4.5-7k-char worker prompt** (~2.7k output tokens at ~110 tok/s on Opus): ~25 s, almost half.
2. **Spawning itself is cheap**: Agent call -> worker's first Telegram edit = ~5 s (worker gets ~55k cached tokens of system/tools/CLAUDE.md, one ToolSearch, one edit).
3. **Trivial scripts are sometimes slow**: `dispatch_start.py` took 11 s in 3/5 cases and `log_ack.py` 7-10 s in 3/5 cases, while `python + sqlite3` alone takes 0.1 s. Not diagnosed yet - candidates: the Telegram sendMessage call inside `dispatch_start` (urlopen timeout 10 s), SQLite lock contention on kai.db with workers writing (default 5 s busy timeout), or Bash-tool / hook overhead on Windows.
4. Each extra dispatcher turn costs ~2-4 s TTFT on the 320k-token main context. Today's flow has 4 turns before the Agent call (dispatch_start, edit+log_ack, Agent, task_acks save after).
5. Worst case (17316, 81 s) also had 17 s before the first tool call (long message, more thinking).

## 2. Options

### Anthropic Managed Agents (Claude API, beta) - does NOT fit
- What it is: Anthropic runs the agent loop and hosts a per-session container; you create an Agent config once and start Sessions (`POST /v1/agents`, sessions, SSE events). Docs: https://platform.claude.com/docs/en/managed-agents/overview
- Cost: API billing (tokens + session running time $0.08/hour, web search $10/1k) - separate from the Claude Code plan.
- Local access: cloud sandboxes cannot reach Omer's PC. Possible only with a **self-hosted sandbox** (outbound-polling worker on the PC) or custom tools executed client-side (https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes-custom-tools) - i.e. re-plumbing kai.db, scripts, WhatsApp bridge, Google tokens and Telegram as custom tools, and losing Claude Code hooks (typing lease, message persistence), CLAUDE.md, memory and the Telegram plugin.
- Session start latency: not documented.
- Verdict: it would not touch the real bottleneck (the dispatcher writing the prompt) and replaces a working local harness with a large rebuild. Same for the Claude Agent SDK as a separate always-on process: it is the same Claude Code harness, so the spawn (5 s) is already not the problem.

### Claude Code-side (https://code.claude.com/docs/en/sub-agents)
- **Predefined subagent `.claude/agents/kai-worker.md`**: the markdown body becomes the system prompt; frontmatter supports `model`, `effort`, `background: true`, `tools`, `mcpServers`, `omitClaudeMd`. The dispatcher then passes only the per-message payload (title, ACK id, original id, Omer's exact message, 1-2 lines of intent). Subagents inherit the main session's MCP tools by default; whether the Telegram **plugin channel** tools are included is not documented -> must be tested before rollout.
- **Agent tool `model` / `effort` per call**: a lighter model (Sonnet 5.5) for simple workers would speed their execution, not the spawn.
- **Fast mode (`/fast`)** for the main session: same Opus, up to 2.5x output speed, at $8/$40 per MTok on Opus 5.5 and needs usage credits on a subscription; toggling invalidates the prompt cache. https://code.claude.com/docs/en/fast-mode
- **`UserPromptSubmit` hook `additionalContext`**: a hook can inject the last-20 context / pending questions before the first turn (default timeout 30 s), and `SubagentStart` can inject context into the worker. https://code.claude.com/docs/en/hooks

### Kai-side
- **Short worker prompt**: worker reads `agents/worker.md` and loads its own context (`dispatch_start.py --no-ack` or a `worker_context.py`) instead of the dispatcher pasting it.
- **One spawn-prep call**: send the ACK already titled ("🔄 <title> - ⏳ מייצר agent...") from `dispatch_start.py --title`, with log + pin + context in the same call -> removes the edit+log_ack turn.
- **Find the 7-11 s script stalls** (timing instrumentation in `dispatch_start.py` / `log_ack.py`, `sqlite3.connect(timeout=...)`, check the Telegram call).
- Keep pushing simple requests to fast scripts / `SendMessage` follow-ups (24 s path).

## 3. Expected savings

| # | change | saves (s) | effort | risk |
|---|---|---|---|---|
| 1 | predefined `kai-worker` subagent + short payload (~300-500 output tokens instead of ~2.7k) | **~18-22** | medium | Telegram plugin tools inside a custom subagent must be verified; worker.md rules move into the agent file |
| 2 | titled ACK + log + pin in `dispatch_start.py` (drop the separate edit+log turn) | **~6-9** | low | dispatcher must choose the title before its first call (it can, from the message) |
| 3 | diagnose and fix the 7-11 s script stalls | **0-15** (avg ~8) | low | none - instrumentation first |
| 4 | `/fast` on the dispatcher | ~10-15 on top of the above if still needed | trivial | ~2x price on main-session tokens, needs usage credits |
| - | Managed Agents / Agent SDK | ~0 for this problem | very high | rebuild of the whole local integration |

## 4. Recommendation

Do 1 + 2 + 3 together: expected total from ~54 s (median 47 s) to **~20-25 s** to "agent מוכן", with no change of platform.
Do not adopt Managed Agents for Kai's workers. Reconsider `/fast` only if the result after 1-3 is still too slow.
