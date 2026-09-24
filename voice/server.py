"""
Kai live voice - local server.

- Serves the voice page (static/index.html).
- Mints single-use Gemini Live ephemeral tokens so the API key never reaches the browser.
- Exposes the fast tools (reminders / tasks / calendar) as direct DB/API calls.
- Runs delegate_to_kai as a headless Claude Code job and mirrors the result to Telegram.

Run: voice/run_voice.ps1  (uses D:/Kai/.venv)
"""
import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import prompt  # noqa: E402
import tools  # noqa: E402
from _base import BOT_TOKEN, CHAT_ID, action_log  # noqa: E402  (path added by tools)

GEMINI_KEY_PATH = Path("D:/Kai/credentials/gemini.json")
CONFIG_PATH = Path("D:/Kai/config/voice.json")
CLAUDE_BIN = os.environ.get("KAI_CLAUDE_BIN", "C:/Users/Omer/.local/bin/claude")
COOKIE = "kai_voice"
API_BASE = "https://generativelanguage.googleapis.com"
WS_URL = (API_BASE.replace("https", "wss") +
          "/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained")

DEFAULT_CONFIG = {
    # Verified against ai.google.dev/gemini-api/docs/models + live-api/capabilities on 2026-09-24:
    #  - gemini-3.8-live: stable, async function calling (NON_BLOCKING) + scheduling supported.
    #  - gemini-2.5-flash-native-audio-preview-12-2025: async supported, but Google limits 2.5
    #    access to accounts that used it before. Switch here if the key has access.
    "model": "gemini-3.8-live",
    "voice": None,               # e.g. "Puck"; None = model default
    "port": 8765,
    "delegate_timeout_sec": 300,
    "delegate_max_parallel": 2,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    if not cfg.get("auth_token"):
        cfg["auth_token"] = secrets.token_urlsafe(24)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


CFG = load_config()
app = FastAPI(title="Kai Voice", docs_url=None, redoc_url=None, openapi_url=None)
_delegate_sem = asyncio.Semaphore(int(CFG["delegate_max_parallel"]))


# ------------------------------------------------------------------ auth

def _authed(request: Request) -> bool:
    supplied = request.cookies.get(COOKIE) or request.headers.get("x-kai-token") or ""
    return secrets.compare_digest(supplied, CFG["auth_token"])


def _require(request: Request):
    if not _authed(request):
        raise HTTPException(401, "unauthorized")


@app.get("/")
def index(request: Request, k: str = ""):
    if k:
        if not secrets.compare_digest(k, CFG["auth_token"]):
            return HTMLResponse("<h3>401</h3>", status_code=401)
        # Move the secret from the URL into a long-lived cookie, then drop it from the address bar.
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, k, max_age=180 * 24 * 3600, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
        return resp
    if not _authed(request):
        return HTMLResponse("<h3 dir=rtl>401 - צריך לפתוח את הקישור עם המפתח</h3>", status_code=401)
    return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health():
    return {"ok": True, "gemini_key": GEMINI_KEY_PATH.exists(), "model": CFG["model"]}


# ------------------------------------------------------------------ Gemini session

def _gemini_key():
    if not GEMINI_KEY_PATH.exists():
        return None
    return json.loads(GEMINI_KEY_PATH.read_text(encoding="utf-8")).get("api_key")


def _mint_token(key, model):
    """POST v1beta/auth_tokens (docs: ai.google.dev/gemini-api/docs/ephemeral-tokens)."""
    now = datetime.now(timezone.utc)
    body = {
        "uses": 1,
        "expireTime": (now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "newSessionExpireTime": (now + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # Lock the token to our model so a leaked token can't be used for anything else.
        "liveConnectConstraints": {"model": f"models/{model}"},
    }
    req = urllib.request.Request(
        f"{API_BASE}/v1beta/auth_tokens", data=json.dumps(body).encode(),
        headers={"x-goog-api-key": key, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["name"]


def build_setup(model, resume_handle=None):
    gen = {"responseModalities": ["AUDIO"]}
    if CFG.get("voice"):
        gen["speechConfig"] = {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": CFG["voice"]}}}
    setup = {
        "model": f"models/{model}",
        "generationConfig": gen,
        "systemInstruction": {"parts": [{"text": prompt.system_instruction()}]},
        "tools": [{"functionDeclarations": prompt.function_declarations()}],
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
        "sessionResumption": {"handle": resume_handle} if resume_handle else {},
        "contextWindowCompression": {"slidingWindow": {}},
    }
    return {"setup": setup}


@app.get("/api/session")
async def new_session(request: Request, resume: str = ""):
    _require(request)
    key = _gemini_key()
    if not key:
        return JSONResponse({"ok": False, "error": "no_gemini_key",
                             "detail": f"missing {GEMINI_KEY_PATH}"}, status_code=503)
    t0 = time.perf_counter()
    try:
        token = await run_in_threadpool(_mint_token, key, CFG["model"])
    except urllib.error.HTTPError as e:
        return JSONResponse({"ok": False, "error": "token_mint_failed",
                             "detail": e.read().decode(errors="replace")[:500]}, status_code=502)
    except Exception as e:
        return JSONResponse({"ok": False, "error": "token_mint_failed", "detail": str(e)}, status_code=502)
    return {"ok": True, "ws_url": f"{WS_URL}?access_token={token}", "setup": build_setup(CFG["model"], resume or None),
            "model": CFG["model"], "mint_ms": round((time.perf_counter() - t0) * 1000)}


# ------------------------------------------------------------------ fast tools

@app.post("/api/tool/{name}")
async def tool(name: str, request: Request):
    _require(request)
    try:
        args = await request.json()
    except Exception:
        args = {}
    t0 = time.perf_counter()
    result = await run_in_threadpool(tools.call_tool, name, args if isinstance(args, dict) else {})
    ms = round((time.perf_counter() - t0) * 1000, 1)
    return JSONResponse(result, headers={"X-Tool-Ms": str(ms)})


# ------------------------------------------------------------------ delegate_to_kai

DELEGATE_SYSTEM = (
    "You are Kai running HEADLESS for Omer's live voice conversation. This is NOT a Telegram message: "
    "ignore the Telegram dispatcher protocol, do not spawn workers, do not send Telegram messages or ACKs yourself, "
    "and never touch typing scripts. Do the task directly and fast. Your final answer is read aloud by a voice agent "
    "and also sent to Omer on Telegram, so reply in Hebrew, concise (max ~6 short lines), no markdown tables, "
    "times in Omer's local time. Destructive or outward actions (sending messages to other people, deleting) "
    "are allowed only if the request states them explicitly; otherwise describe what you would do and stop."
)

# Headless flags: user settings only (no project hooks -> no SessionStart Telegram ping, no typing lease,
# no plugin patching); Telegram plugin disabled so a second bot poller never fights the live channel.
CLAUDE_ARGS = [
    "-p", "--output-format", "json", "--no-session-persistence",
    "--permission-mode", "bypassPermissions",
    "--setting-sources", "user",
    "--settings", json.dumps({"enabledPlugins": {"telegram@claude-plugins-official": False}}),
    "--strict-mcp-config",
    "--append-system-prompt", DELEGATE_SYSTEM,
]


def _run_claude(task, timeout):
    t0 = time.perf_counter()
    try:
        p = subprocess.run([CLAUDE_BIN, *CLAUDE_ARGS], input=task, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd="D:/Kai", timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return False, f"הבדיקה לקחה יותר מ-{timeout // 60} דקות ונעצרה.", time.perf_counter() - t0
    secs = time.perf_counter() - t0
    try:
        data = json.loads(p.stdout)
        text = (data.get("result") or "").strip()
        return (not data.get("is_error") and bool(text)), text or "(אין תשובה)", secs
    except Exception:
        return False, (p.stdout or p.stderr or "claude failed")[-800:], secs


def _telegram(text):
    body = json.dumps({"chat_id": CHAT_ID, "text": text[:4000]}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=body,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("result", {}).get("message_id")
    except Exception:
        return None


def _save_outgoing(msg_id, text):
    """Keep the messages table complete (same as fast scripts' tg_reply)."""
    if not msg_id:
        return
    try:
        conn = tools.get_db()
        conn.execute("INSERT INTO messages (telegram_message_id, direction, text, ts) VALUES (?, 'outgoing', ?, ?)",
                     (int(msg_id), text, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()
    except Exception:
        pass


async def _delegate_job(task):
    async with _delegate_sem:
        ok, text, secs = await run_in_threadpool(_run_claude, task, int(CFG["delegate_timeout_sec"]))
    icon = "🎙️" if ok else "⚠️"
    tg_text = f"\u200F{icon} מהשיחה הקולית: {task}\n\n{text}"
    msg_id = await run_in_threadpool(_telegram, tg_text)
    await run_in_threadpool(_save_outgoing, msg_id, tg_text)
    action_log("voice", "delegate", f"ok={ok} secs={secs:.1f} task={task[:200]}")
    return {"ok": ok, "result": text, "seconds": round(secs, 1), "telegram_sent": bool(msg_id)}


@app.post("/api/delegate")
async def delegate(request: Request):
    _require(request)
    body = await request.json()
    task = (body.get("task") or "").strip()
    if not task:
        return {"ok": False, "result": "empty task"}
    # Shielded: if the browser disconnects mid-job, the job still finishes and reaches Telegram.
    return await asyncio.shield(asyncio.ensure_future(_delegate_job(task)))


@app.on_event("startup")
async def warm():
    # Build the Calendar client once so the first "what's on today" doesn't pay ~2s.
    asyncio.get_running_loop().run_in_executor(None, lambda: tools.call_tool("get_calendar", {"range": "today"}))


if __name__ == "__main__":
    import uvicorn
    print(f"Kai voice on http://127.0.0.1:{CFG['port']}/?k={CFG['auth_token']}")
    uvicorn.run(app, host="127.0.0.1", port=int(CFG["port"]), log_level="info")
