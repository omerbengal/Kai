#!/usr/bin/env python3
"""
Transcribe an audio file: the local Hebrew model first, Groq whisper-large-v3 as fallback.

Since 30/10/2026 the local service scripts/asr_server.py (ivrit.ai Hebrew Whisper on the
GPU, 127.0.0.1:8787) is tried first; on ANY failure - service down, still loading, slow,
error, empty text - the Groq path below runs exactly as before. Each run logs the engine
used to action_log (agent='transcribe', action='asr_local' | 'asr_groq').
Usage: python transcribe.py <audio_file_path> [--ack_id MSG_ID] [--chat_id CHAT_ID]

If --ack_id is provided, edits the Telegram ACK message with the transcription
immediately after it completes — no need to wait for Claude to do it.
Prints the transcribed text to stdout regardless.
"""

import sys
import os
import subprocess
import json
import urllib.request
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net_ipv4  # noqa: F401  IPv6 to Telegram is broken here: +10s per call without this


GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
BOT_TOKEN = os.environ["KAI_TELEGRAM_BOT_TOKEN"]

INITIAL_PROMPT = (
    "שיחה בעברית עם עוזר אישי בשם קאי. הדובר מערבב לפעמים מילים באנגלית. "
    "שמות: <partner>, <contact>, <contact>. "
    "שעות בפורמט 7:53, 16:30, 20:00. "
    "מונחים: קאי, טבלה, ACK, listener.md, worker, agent, CLAUDE.md, תזכורת, משימה."
)

# Shared with the live voice agent (voice/prompt.py): Omer's slang and names, one per line.
# Before 26/09 only Gemini Live read it, so Telegram voice notes turned נואיד into נועיד.
VOCAB_PATH = "D:/Kai/config/voice_vocabulary.txt"

# Whisper's prompt is only a hint, so known mishearings are also fixed after transcription.
CORRECTIONS = {
    "נועיד": "נואיד",
}


def load_vocabulary() -> list:
    try:
        with open(VOCAB_PATH, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]
    except Exception:
        return []


def build_prompt() -> str:
    vocab = load_vocabulary()
    return INITIAL_PROMPT + (" מילים: " + ", ".join(vocab) + "." if vocab else "")


def apply_corrections(text: str) -> str:
    for wrong, right in CORRECTIONS.items():
        text = text.replace(wrong, right)
    return text


LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 8787
LOCAL_CONNECT_TIMEOUT = 0.5
# Telegram voice notes (Opus) run ~3.8 KB per second of audio; dividing by 3000 errs long,
# which only makes the read timeout a little more generous.
BYTES_PER_AUDIO_SECOND = 3000


def local_read_timeout(audio_path: str) -> float:
    try:
        seconds = os.path.getsize(audio_path) / BYTES_PER_AUDIO_SECOND
    except OSError:
        seconds = 0
    return max(5.0, 0.3 * seconds)


def transcribe_local(audio_path: str) -> str:
    """Ask the local service. Raises on any problem so the caller falls back to Groq."""
    import http.client
    conn = http.client.HTTPConnection(LOCAL_HOST, LOCAL_PORT, timeout=LOCAL_CONNECT_TIMEOUT)
    try:
        conn.connect()
        conn.sock.settimeout(local_read_timeout(audio_path))
        body = json.dumps({"path": os.path.abspath(audio_path)}, ensure_ascii=False).encode("utf-8")
        conn.request("POST", "/transcribe", body=body,
                     headers={"Content-Type": "application/json; charset=utf-8"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()
    if resp.status != 200:
        raise RuntimeError("local asr %s: %s" % (resp.status, data.get("error")))
    text = (data.get("text") or "").strip()
    if not text:
        raise RuntimeError("local asr returned empty text")
    return text


def log_engine(action: str, details: str):
    try:
        import sqlite3
        conn = sqlite3.connect("D:/Kai/kai.db", timeout=2)
        conn.execute("INSERT INTO action_log (agent, action, details) VALUES ('transcribe', ?, ?)",
                     (action, details))
        conn.commit()
        conn.close()
    except Exception:
        pass  # logging must never break a transcription


def transcribe(audio_path: str) -> str:
    """Local Hebrew model first; Groq on any failure. Same return value either way."""
    import time
    name = os.path.basename(audio_path)
    t = time.time()
    try:
        text = apply_corrections(transcribe_local(audio_path))
        log_engine("asr_local", "%s %.2fs" % (name, time.time() - t))
        return text
    except Exception as e:
        reason = "%s: %s" % (type(e).__name__, e)
    t = time.time()
    text = transcribe_groq(audio_path)
    log_engine("asr_groq", "%s %.2fs (local failed: %s)" % (name, time.time() - t, reason[:200]))
    return text


def transcribe_groq(audio_path: str) -> str:
    result = subprocess.run(
        [
            "curl", "-s", "-X", "POST", GROQ_URL,
            "-H", f"Authorization: Bearer {GROQ_API_KEY}",
            "-F", f"file=@{audio_path};type=audio/ogg;filename=audio.ogg",
            "-F", "model=whisper-large-v3",
            "-F", "language=he",
            "-F", f"prompt={build_prompt()}",
        ],
        capture_output=True,
        timeout=60,
    )
    data = json.loads(result.stdout.decode("utf-8"))
    if "error" in data:
        raise RuntimeError(data["error"]["message"])
    return apply_corrections(data["text"].strip())


def tg_edit(chat_id: str, msg_id: str, text: str):
    payload = json.dumps({
        "chat_id": chat_id,
        "message_id": int(msg_id),
        "text": text,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


ACK_TEXT = "🎤 מתמלל... ⏳"
ACK_MAX_AGE_MINUTES = 10


def find_recent_ack():
    """
    Locate the "transcribing..." message this run is almost certainly for.

    --ack_id is the intended way to say which message to replace, but forgetting
    it is silent and invisible from the caller's side: the transcription still
    prints, so everything looks fine, while Omer is left staring at
    "מתמלל... ⏳" forever. That happened five times in one day before he noticed.

    So when the flag is missing, recover the id instead of skipping the edit.
    The ACK text is a fixed string sent moments earlier, which makes the newest
    outgoing message carrying it an unambiguous match. The age limit keeps an old
    never-updated ACK from being rewritten with an unrelated transcription long
    after the fact - better to do nothing than to edit the wrong message.
    """
    try:
        import sqlite3
        from datetime import datetime, timezone, timedelta
        conn = sqlite3.connect("D:/Kai/kai.db")
        conn.text_factory = str
        row = conn.execute(
            "SELECT telegram_message_id, ts FROM messages "
            "WHERE direction = 'outgoing' AND text = ? "
            "ORDER BY id DESC LIMIT 1",
            (ACK_TEXT,),
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            return None
        stamped = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
        if stamped.tzinfo is None:
            stamped = stamped.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - stamped
        if age > timedelta(minutes=ACK_MAX_AGE_MINUTES):
            return None
        return str(row[0])
    except Exception:
        return None  # transcription must still succeed even if the lookup breaks


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser()
    p.add_argument("audio_path")
    p.add_argument("--ack_id", default=None, help="Telegram message ID to edit with transcription")
    p.add_argument("--chat_id", default=os.environ.get("KAI_TELEGRAM_CHAT_ID", ""), help="Telegram chat ID")
    p.add_argument("--no_ack_lookup", action="store_true",
                   help="Do not fall back to finding the ACK message automatically")
    args = p.parse_args()

    text = transcribe(args.audio_path)

    ack_id = args.ack_id
    if not ack_id and not args.no_ack_lookup:
        ack_id = find_recent_ack()
        if ack_id:
            print("[transcribe] --ack_id missing; edited recovered ACK %s" % ack_id,
                  file=sys.stderr)

    if ack_id:
        tg_edit(args.chat_id, ack_id, f'\u200F🎤 "{text}"')
        try:
            import sqlite3
            conn = sqlite3.connect("D:/Kai/kai.db")
            # Also stops this row matching find_recent_ack() on the next voice message.
            conn.execute("UPDATE messages SET text = ? WHERE telegram_message_id = ?",
                         (f'🎤 "{text}"', int(ack_id)))
            conn.commit()
            conn.close()
        except Exception:
            pass

    print(text)
