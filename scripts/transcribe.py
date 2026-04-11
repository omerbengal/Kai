#!/usr/bin/env python3
"""
Transcribe an audio file using Groq's whisper-large-v3 API.
Usage: python transcribe.py <audio_file_path>
Prints the transcribed text to stdout.
"""

import sys
import os
import subprocess
import json


GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


def transcribe(audio_path: str) -> str:
    result = subprocess.run(
        [
            "curl", "-s", "-X", "POST", GROQ_URL,
            "-H", f"Authorization: Bearer {GROQ_API_KEY}",
            "-F", f"file=@{audio_path};type=audio/ogg;filename=audio.ogg",
            "-F", "model=whisper-large-v3",
            "-F", "language=he",
        ],
        capture_output=True,
        timeout=60,
    )
    data = json.loads(result.stdout.decode("utf-8"))
    if "error" in data:
        raise RuntimeError(data["error"]["message"])
    return data["text"].strip()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print("Usage: python transcribe.py <audio_file_path>", file=sys.stderr)
        sys.exit(1)
    print(transcribe(sys.argv[1]))
