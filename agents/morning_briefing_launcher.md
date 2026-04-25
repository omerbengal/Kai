# Morning Briefing — Cron Launcher

This file is the cron trigger for the daily morning briefing (09:00 Jerusalem).

**You are the main session (dispatcher). Do NOT execute the briefing yourself.**

Read the file `D:/Kai/agents/morning_briefing.md` and spawn a **background Agent** with its content as the prompt. Then stop immediately.

```python
# Read morning_briefing.md and pass its content to the agent
with open('D:/Kai/agents/morning_briefing.md', encoding='utf-8') as f:
    briefing_prompt = f.read()
```

Then call `Agent(prompt=briefing_prompt, run_in_background=True)`.

That's all — the agent handles everything from there (DB queries, weather, calendar, email, Telegram send).
