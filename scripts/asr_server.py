#!/usr/bin/env python3
"""
Local Hebrew speech-to-text service for Kai (30/10/2026).

Keeps the ivrit.ai Hebrew Whisper model (whisper-large-v3-turbo fine-tune, CTranslate2)
loaded on the GPU so a voice note transcribes in ~1s instead of ~7s for a cold load.
scripts/transcribe.py calls it first and falls back to Groq on any failure.

Run with the venv (system Python 3.9 has no faster-whisper):
    D:/Kai/.venv/Scripts/pythonw.exe D:/Kai/scripts/asr_server.py
Started and kept alive by the KaiAsrServer scheduled task (logon + every 5 min,
IgnoreNew): if the server is up the task instance simply is it; after a crash the
next 5-minute trigger starts a new one. A second copy exits at once because the
port is taken.

Endpoints (127.0.0.1 only):
    GET  /health      -> {"ok": true, "ready": bool, "model": ..., "uptime_s": ...}
    POST /transcribe  {"path": "<local audio file>"} -> {"text", "duration", "secs"}
                      503 while the model is still loading.

Requirement: ctranslate2 needs cuBLAS 12 (cublas64_12.dll). It comes from the pip
package nvidia-cublas-cu12 in the venv, and its bin folder must be on PATH before
ctranslate2 is imported - done below, so no PATH setup is needed by the caller.

Turn off: disable the KaiAsrServer task and stop the process - transcribe.py then
uses Groq automatically.
"""

import json
import os
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 8787
MODEL = "ivrit-ai/whisper-large-v3-turbo-ct2"
LOG_PATH = "D:/Kai/tmp/asr_server.log"
CUBLAS_BIN = os.path.join(os.path.dirname(sys.executable), "..", "Lib", "site-packages", "nvidia", "cublas", "bin")

# Tuned on 8 of Omer's voice notes (30/10/2026). Kai's long Groq vocabulary prompt made
# this model invent whole sentences ("הצעות ועדת הכספים לסדר-היום"); this short one -
# names plus one sentence showing English words in Latin letters - did not.
PROMPT = "קאי, <partner>, <contact>, נואיד. תבדוק ב-dashboard את ה-agent וב-WhatsApp."
OPTIONS = dict(
    language="he", beam_size=5, initial_prompt=PROMPT,
    condition_on_previous_text=False, vad_filter=True,
    hallucination_silence_threshold=1.0, no_speech_threshold=0.6,
    compression_ratio_threshold=2.2, temperature=0.0,
)
BIDI_MARKS = re.compile("[\u202a-\u202e]")  # the model sometimes emits RLE/PDF marks

_model = None
_lock = threading.Lock()  # one GPU transcription at a time
_started = time.time()


def log(msg):
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 1_000_000:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg + "\n")
    except Exception:
        pass


def load_model():
    global _model
    try:
        cublas = os.path.abspath(CUBLAS_BIN)
        os.environ["PATH"] = cublas + os.pathsep + os.environ.get("PATH", "")
        from faster_whisper import WhisperModel
        t = time.time()
        m = WhisperModel(MODEL, device="cuda", compute_type="float16")
        # First call pays the CUDA kernel set-up; do it now, not on Omer's first note.
        import numpy as np
        noise = (np.random.RandomState(0).randn(16000 * 3) * 0.01).astype("float32")
        list(m.transcribe(noise, language="he", beam_size=5, vad_filter=False)[0])
        _model = m
        log("model ready in %.1fs" % (time.time() - t))
    except Exception as e:
        log("MODEL LOAD FAILED: %r" % (e,))
        os._exit(1)  # let the scheduled task start a fresh process


def transcribe(path):
    with _lock:
        t = time.time()
        segs, info = _model.transcribe(path, **OPTIONS)
        text = " ".join(s.text.strip() for s in segs)
        secs = time.time() - t
    text = BIDI_MARKS.sub("", text).strip()
    return {"text": text, "duration": round(info.duration, 1), "secs": round(secs, 2)}


class Server(ThreadingHTTPServer):
    # HTTPServer turns on SO_REUSEADDR, which on Windows lets a SECOND process bind the
    # same port - the single-instance guard below depends on the bind failing.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence the default stderr access log (pythonw has none)
        pass

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "ready": _model is not None, "model": MODEL,
                             "uptime_s": int(time.time() - _started)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/transcribe":
            return self._send(404, {"error": "not found"})
        if _model is None:
            return self._send(503, {"error": "model loading"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            path = json.loads(self.rfile.read(n).decode("utf-8"))["path"]
            if not os.path.isfile(path):
                return self._send(400, {"error": "no such file"})
            res = transcribe(path)
            log("ok %s %.1fs audio in %.2fs" % (os.path.basename(path), res["duration"], res["secs"]))
            self._send(200, res)
        except Exception as e:
            log("ERROR %r" % (e,))
            self._send(500, {"error": str(e)})


def main():
    # Bind before loading the model: a second copy (the task's 5-minute trigger, or a
    # manual run) must exit within a second instead of taking another 1.8GB of VRAM.
    try:
        srv = Server((HOST, PORT), Handler)
    except OSError:
        return 0
    log("listening on %s:%d, loading model" % (HOST, PORT))
    threading.Thread(target=load_model, daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
