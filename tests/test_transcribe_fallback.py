"""transcribe.py: the local Hebrew service first, Groq on any failure (30/10/2026)."""
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, "D:/Kai/scripts")
import transcribe as T  # noqa: E402


@pytest.fixture
def audio(tmp_path):
    p = tmp_path / "note.oga"
    p.write_bytes(b"\0" * 30000)  # ~10s by the size estimate
    return str(p)


@pytest.fixture(autouse=True)
def no_side_effects(monkeypatch):
    logged = []
    monkeypatch.setattr(T, "log_engine", lambda a, d: logged.append((a, d)))
    monkeypatch.setattr(T, "transcribe_groq", lambda p: "groq text")
    return logged


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def serve(monkeypatch, handler_fn):
    """Start a throwaway HTTP server on a free port and point transcribe.py at it."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            code, obj, delay = handler_fn(self)
            time.sleep(delay)
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                pass

    port = free_port()
    srv = HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(T, "LOCAL_PORT", port)
    return srv


def test_local_service_used_when_healthy(monkeypatch, audio, no_side_effects):
    seen = {}

    def ok(h):
        seen["body"] = json.loads(h.rfile.read(int(h.headers["Content-Length"])))
        return 200, {"text": "שלום נועיד", "duration": 10, "secs": 0.5}, 0

    srv = serve(monkeypatch, ok)
    try:
        assert T.transcribe(audio) == "שלום נואיד"  # fix-up list still applied
    finally:
        srv.shutdown()
    assert seen["body"]["path"].endswith("note.oga")
    assert no_side_effects[0][0] == "asr_local"


def test_service_down_falls_back_to_groq(monkeypatch, audio, no_side_effects):
    monkeypatch.setattr(T, "LOCAL_PORT", free_port())  # nothing listening
    t = time.time()
    assert T.transcribe(audio) == "groq text"
    assert time.time() - t < 1.5
    assert no_side_effects[0][0] == "asr_groq"


def test_model_loading_503_falls_back(monkeypatch, audio, no_side_effects):
    srv = serve(monkeypatch, lambda h: (503, {"error": "model loading"}, 0))
    try:
        assert T.transcribe(audio) == "groq text"
    finally:
        srv.shutdown()
    assert "503" in no_side_effects[0][1]


def test_server_error_falls_back(monkeypatch, audio, no_side_effects):
    srv = serve(monkeypatch, lambda h: (500, {"error": "boom"}, 0))
    try:
        assert T.transcribe(audio) == "groq text"
    finally:
        srv.shutdown()


def test_empty_text_falls_back(monkeypatch, audio, no_side_effects):
    srv = serve(monkeypatch, lambda h: (200, {"text": "  "}, 0))
    try:
        assert T.transcribe(audio) == "groq text"
    finally:
        srv.shutdown()


def test_slow_service_times_out_and_falls_back(monkeypatch, audio, no_side_effects):
    monkeypatch.setattr(T, "local_read_timeout", lambda p: 0.5)
    srv = serve(monkeypatch, lambda h: (200, {"text": "late"}, 2))
    try:
        t = time.time()
        assert T.transcribe(audio) == "groq text"
        assert time.time() - t < 1.8
    finally:
        srv.shutdown()
    assert "timed out" in no_side_effects[0][1] or "Timeout" in no_side_effects[0][1]


def test_read_timeout_scales_with_audio_length(tmp_path):
    short = tmp_path / "s.oga"
    short.write_bytes(b"\0" * 10000)        # ~3s
    long_ = tmp_path / "l.oga"
    long_.write_bytes(b"\0" * 600000)       # ~200s
    assert T.local_read_timeout(str(short)) == 5.0
    assert T.local_read_timeout(str(long_)) == pytest.approx(60.0)
