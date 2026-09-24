# Kai live voice (POC)

A web page where Omer talks live with Kai. Gemini Live handles the voice. A local FastAPI server exposes fast tools and hands heavy requests to Claude Code.

```
iPhone Safari --HTTPS (tailscale serve)--> server.py :8765 --> kai.db / Google Calendar   (fast tools, ~1-400 ms)
      |                                         '-> claude -p (headless)  --> result spoken + Telegram
      '--WebSocket (ephemeral token)--> Gemini Live API (gemini-3.8-live)
```

## Files
- `server.py` - FastAPI app: page, auth, ephemeral-token minting, `/api/tool/{name}`, `/api/delegate`.
- `tools.py` - fast tools: direct kai.db / Calendar calls. Reuses `scripts/fast/_base.py`, `_gcal.py` and `scripts/local_tz.py`. Never spawns fast scripts and never sends Telegram messages.
- `prompt.py` - system instruction and function declarations. `delegate_to_kai` is `NON_BLOCKING`; its result comes back with `scheduling: WHEN_IDLE`.
- `static/index.html` - the RTL page: mic button, status, live transcript, raw WebSocket client (mic 16 kHz PCM in, 24 kHz PCM out).
- `test_tools.py` - tool tests against the real kai.db. Test rows are marked `[VOICE-TEST]` and deleted afterwards.
- `run_voice.ps1` / `run_voice.bat` - start the server with the project venv.

## Config
- `D:/Kai/credentials/gemini.json`: `{"api_key": "..."}` (gitignored). Without it, `/api/session` returns 503 `no_gemini_key`.
- `D:/Kai/config/voice.json` (gitignored): created on first run. It holds `auth_token` (the URL secret), `model`, `voice`, `port`, `delegate_timeout_sec` and `delegate_max_parallel`.

## Model choice (checked on ai.google.dev, 24/09/2026)
- `gemini-3.8-live` (stable) is the default. The capabilities page lists it with async function calling (`NON_BLOCKING`) and scheduling `SILENT` / `WHEN_IDLE`.
- The 2.5 Flash Live id is `gemini-2.5-flash-native-audio-preview-12-2025`. It also supports async calls, but Google now limits 2.5 access to accounts that used it before, so a new key probably can't use it. You can switch models in `config/voice.json`.
- `gemini-3.1-flash-live-preview` does not support async calls, so don't use it.

## Access
1. Run `voice/run_voice.ps1`. It prints `http://127.0.0.1:8765/?k=<token>`.
2. Run `tailscale serve --bg 8765` to get `https://<pc>.<tailnet>.ts.net/`. Open `https://<pc>.<tailnet>.ts.net/?k=<token>` once. The server moves the key into a cookie and redirects to `/`.
3. Use `127.0.0.1`, not `localhost`, for local tests. On this PC, `localhost` adds about 200 ms because it tries IPv6 first.

## delegate_to_kai
The server runs `claude -p --output-format json --permission-mode bypassPermissions --setting-sources user --strict-mcp-config` from `D:/Kai` with the Telegram plugin disabled. That means no project hooks (no startup ping, no typing lease) and no second bot poller competing with the live channel. The timeout is 300 s and at most 2 jobs run in parallel. The result goes back to the live session and is also sent to Telegram chat ${KAI_TELEGRAM_CHAT_ID}.

## Measured (24/09/2026, over HTTP on 127.0.0.1)
- list reminders / tasks: 1-20 ms.
- create / reschedule / cancel / done: 70-320 ms. The DB commit is the main cost, and the audit log is written off-thread.
- calendar: 220-480 ms. The first call pays about 1-2 s to build the client, so the server warms it at startup.
- delegate (simple DB question): about 6 s end to end.
- NOT tested: the Gemini Live session itself. No API key yet.

## Known limits
- Gemini Live sessions are capped (15 min audio-only). The page uses session resumption plus `goAway` to reconnect with a fresh token.
- With the free Gemini tier, Google may use the content and have humans review it (ai.google.dev/gemini-api/terms, "Unpaid Services"). A paid tier avoids that.
