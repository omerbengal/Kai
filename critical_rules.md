# כללים קריטיים — תזכורת אוטומטית

1. **ACK timing**: Delete ACK only AFTER sending the final reply. Order: tg_reply → tg_delete. Never delete first. Never call start_typing/stop_typing — the hooks own the typing indicator (investigation #043).

2. **Silent edits forbidden**: Any Edit or Write tool call made in response to Omer's Telegram message MUST be followed immediately by a Telegram reply: "✅ [what changed]". Omer watches Telegram not terminal.

3. **pending_question.json**: At Step -1 of EVERY message, check D:/Kai/tmp/pending_question.json. If non-empty and current message doesn't answer the pending question, send it as a SEPARATE reply after handling the main task.

4. **All responses via MCP**: Omer does NOT see session output. Every response — trivial or not — MUST go through mcp__plugin_telegram_telegram__reply or react. Never output text to session as a response.
