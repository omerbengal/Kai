#!/usr/bin/env python3
"""
Transcribe an audio file using Groq's whisper-large-v3 API.
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


GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
BOT_TOKEN = os.environ["KAI_TELEGRAM_BOT_TOKEN"]

INITIAL_PROMPT = (
    "שיחה בעברית עם עוזר אישי בשם קאי. הדובר מערבב לפעמים מילים באנגלית. "
    "שמות: <partner>, <contact>, <contact>. "
    "שעות בפורמט 7:53, 16:30, 20:00. "
    "מונחים: קאי, טבלה, ACK, listener.md, worker, agent, CLAUDE.md, תזכורת, משימה."
)


def transcribe(audio_path: str) -> str:
    result = subprocess.run(
        [
            "curl", "-s", "-X", "POST", GROQ_URL,
            "-H", f"Authorization: Bearer {GROQ_API_KEY}",
            "-F", f"file=@{audio_path};type=audio/ogg;filename=audio.ogg",
            "-F", "model=whisper-large-v3",
            "-F", f"prompt={INITIAL_PROMPT}",
        ],
        capture_output=True,
        timeout=60,
    )
    data = json.loads(result.stdout.decode("utf-8"))
    if "error" in data:
        raise RuntimeError(data["error"]["message"])
    return data["text"].strip()


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


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser()
    p.add_argument("audio_path")
    p.add_argument("--ack_id", default=None, help="Telegram message ID to edit with transcription")
    p.add_argument("--chat_id", default=os.environ.get("KAI_TELEGRAM_CHAT_ID", ""), help="Telegram chat ID")
    args = p.parse_args()

    text = transcribe(args.audio_path)

    if args.ack_id:
        tg_edit(args.chat_id, args.ack_id, f'\u200F🎤 "{text}"')
        try:
            import sqlite3
            conn = sqlite3.connect("D:/Kai/kai.db")
            conn.execute("UPDATE messages SET text = ? WHERE telegram_message_id = ?",
                         (f'🎤 "{text}"', int(args.ack_id)))
            conn.commit()
            conn.close()
        except Exception:
            pass

    print(text)
