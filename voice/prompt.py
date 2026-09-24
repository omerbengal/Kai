"""System instruction and function declarations for the Gemini Live voice agent."""
import tools

S, O = "STRING", "OBJECT"


def _fn(name, description, props=None, required=None, behavior="BLOCKING"):
    d = {"name": name, "description": description, "behavior": behavior}
    if props:
        d["parameters"] = {"type": O, "properties": props, "required": required or []}
    return d


def _p(type_, description, enum=None):
    p = {"type": type_, "description": description}
    if enum:
        p["enum"] = enum
    return p


LOCAL_FMT = "Omer's LOCAL time, format 'YYYY-MM-DD HH:MM' (24h). Resolve relative times (tomorrow, in an hour) yourself from the current time in the system instruction."


def function_declarations():
    return [
        _fn("get_time", "Current local date, time and timezone."),
        _fn("list_reminders", "List Omer's pending reminders (including overdue ones still pending), with ids.",
            {"days": _p("INTEGER", "How many days ahead. Default 30.")}),
        _fn("create_reminder", "Create a reminder.",
            {"message": _p(S, "Reminder text, in Hebrew, as Omer would write it."),
             "when_local": _p(S, LOCAL_FMT),
             "recurrence": _p(S, "Optional repeat.", ["daily", "weekly", "monthly", "yearly"])},
            ["message", "when_local"]),
        _fn("reschedule_reminder", "Move a pending reminder to a new time and/or change its text. Prefer reminder_id from list_reminders; otherwise a keyword from its text. If the result says 'ambiguous', ask Omer which one.",
            {"reminder_id": _p("INTEGER", "Reminder id."),
             "keyword": _p(S, "Word from the reminder text, if no id."),
             "new_when_local": _p(S, "New time. " + LOCAL_FMT),
             "new_message": _p(S, "New text, only if Omer asked to change it.")}),
        _fn("cancel_reminder", "Cancel a pending reminder. DESTRUCTIVE: only after Omer explicitly confirmed in this conversation.",
            {"reminder_id": _p("INTEGER", "Reminder id."),
             "keyword": _p(S, "Word from the reminder text, if no id.")}),
        _fn("list_tasks", "List Omer's open tasks, with ids.",
            {"filter": _p(S, "Which tasks.", ["all", "today", "overdue"])}),
        _fn("create_task", "Create a task.",
            {"title": _p(S, "Short task title in Hebrew."),
             "priority": _p("INTEGER", "0-11. 0-4 low, 5-9 medium (default 5), 10-11 critical."),
             "domain": _p(S, "Optional area.", ["work", "personal", "health", "finance", "family", "friends", "hobbies"]),
             "due_local": _p(S, "Optional due time. " + LOCAL_FMT)},
            ["title"]),
        _fn("complete_task", "Mark an open task as done. Prefer task_id from list_tasks. Confirm with Omer first if it is not obvious which task he means.",
            {"task_id": _p("INTEGER", "Task id."),
             "keyword": _p(S, "Word from the task title, if no id.")}),
        _fn("get_calendar", "Read Omer's Google Calendar. 'today' = the whole local day, 'tomorrow', or 'upcoming' = from now for N days.",
            {"range": _p(S, "Which window.", ["today", "tomorrow", "upcoming"]),
             "days": _p("INTEGER", "For 'upcoming' only. Default 7.")}),
        _fn("delegate_to_kai",
            "Hand a heavy request to the full Kai assistant (Claude, with web search, WhatsApp, email, files, memory, everything). "
            "Use for anything the other tools can't do: research, news, prices, messages to people, email, looking things up. "
            "It runs in the background and can take 30 seconds to a few minutes; you get the answer later. "
            "Right after calling it, tell Omer briefly that you're checking and it will take a moment, and keep the conversation going.",
            {"task": _p(S, "The full, self-contained request in Hebrew, with every detail Omer gave (names, dates, what exactly he wants).")},
            ["task"], behavior="NON_BLOCKING"),
    ]


def system_instruction():
    n = tools.now_info()
    travel = f" Omer is abroad; local time is {n['travel_label']} time." if n.get("travel_label") else ""
    return f"""You are Kai (קאי), Omer's personal assistant, now talking with him live by voice.
Current local time: {n['now_local']} ({n['weekday']}), timezone {n['timezone']}.{travel}

Style:
- Speak Hebrew by default (switch to English only if Omer speaks English). Casual, warm, like a sharp friend.
- Keep answers SHORT and conversational: one or two sentences. Summarize lists (say how many, read the top 3, offer more). Never read ids, raw timestamps or years aloud; say times naturally ("מחר בשמונה בערב").
- Omer is a software developer; skip basics.

Rules:
- Never invent data. Reminders, tasks and calendar come ONLY from the tools. If a tool fails, say so plainly.
- All times are Omer's local time. Pass times to tools as 'YYYY-MM-DD HH:MM'.
- Destructive actions (cancel_reminder, complete_task when unclear, anything that deletes or sends to other people): first say exactly what you are about to do and wait for a clear yes.
- After creating or changing something, confirm in one short sentence (what + when).
- If a tool returns 'ambiguous', read the candidates briefly and ask which one.
- For anything beyond reminders, tasks and calendar (questions needing the web, news, messages to people, email, WhatsApp, research, memory about people), call delegate_to_kai with a complete, self-contained request, then immediately say something like "אני בודק, זה ייקח רגע" and keep talking. When its result arrives, tell Omer the gist in one or two sentences (the full text also goes to his Telegram).
- Don't delegate what you can answer from the fast tools, and don't answer from your own knowledge anything that depends on current facts.
"""
