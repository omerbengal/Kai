# Kai worker protocol - moved

Since 19/10/2026 (spawn speedup, `docs/agent_spawn_latency.md`) the full worker protocol lives in
**`D:/Kai/.claude/agents/kai-worker.md`** - the single source of truth. Edit it there, never here.

- Normal path: the dispatcher spawns `Agent(subagent_type="kai-worker", prompt=<short task block>)`.
  The agent file's body is the worker's system prompt, so the dispatcher no longer pastes it.
- Fallback (kai-worker unavailable, e.g. the agent file failed to load): spawn a `general-purpose`
  agent whose prompt is: "Read D:/Kai/.claude/agents/kai-worker.md (skip the frontmatter) and follow it
  as your instructions. Your task message:" + the same short task block.
- References elsewhere to "worker.md -> <section>" (Public repo sync, RTL, voice format, completion
  block) mean the same section in `kai-worker.md`.
