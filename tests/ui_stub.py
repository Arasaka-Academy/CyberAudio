"""UI automation fixture only. Never bundled in the production application."""
import argparse
import io
import json
import math
import os
from pathlib import Path
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import wave

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--parent-pid", type=int, required=True)
parser.add_argument("--instance", required=True)
args = parser.parse_args()
config = json.loads((args.root / "config/service.json").read_text())
records = {}
started = time.monotonic()
busy = threading.Event()


def fixture_options():
    path = args.root / "config/ui-fixture.json"
    return json.loads(path.read_text()) if path.exists() else {}


def record(value):
    path = args.root / "reports/ui-requests.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as output:
        output.write(json.dumps(value) + "\n")


pcm = b"".join(struct.pack("<h", int(3000 * math.sin(i * 2 * math.pi * 440 / 24000))) for i in range(24000))
out = io.BytesIO()
with wave.open(out, "wb") as audio:
    audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
    audio.writeframes(pcm)
wav = out.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def respond(self, data, headers=None):
        if isinstance(data, dict): data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {"Content-Type": "application/json"}).items(): self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/v1/requests/"):
            self.respond({"status": "completed", "duration": 1})
        else:
            options = fixture_options()
            status = options.get("status", "ready")
            if time.monotonic() - started < options.get("startup_delay", 0):
                status = "loading"
            self.respond({"status": status, "busy": busy.is_set(), "instance": args.instance, "startup": {"load_ms": 1, "warmup_ms": 1}})

    def do_POST(self):
        payload = self.rfile.read(int(self.headers["Content-Length"]))
        options = fixture_options()
        busy.set()
        value = {}
        try:
            if not self.path.endswith("transcriptions"):
                value = json.loads(payload)
            record({"event": "started", "path": self.path, "stream": value.get("stream"), "voice": value.get("voice")})
            time.sleep(options.get("response_delay", 0))
            if options.get("fail"):
                self.send_response(500)
                data = json.dumps({"error": {"code": "ui_fixture", "message": "界面测试：生成失败，请重试。"}}).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif self.path.endswith("transcriptions"):
                self.respond({"text": "这是 CyberAudio 的界面测试结果。", "language": "zh", "duration": 1, "processing_ms": 12})
            elif value.get("stream"):
                self.send_response(200)
                self.send_header("Content-Length", str(len(pcm)))
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("X-Request-ID", "ui-test")
                self.send_header("X-Audio-Sample-Rate", "24000")
                self.end_headers()
                for offset in range(0, len(pcm), 4800):
                    self.wfile.write(pcm[offset:offset + 4800])
                    self.wfile.flush()
                    time.sleep(options.get("chunk_delay", 0))
            else:
                self.respond(wav, {"Content-Type": "audio/wav"})
            record({"event": "completed", "path": self.path})
        except (BrokenPipeError, ConnectionResetError):
            record({"event": "disconnected", "path": self.path})
        finally:
            busy.clear()


server = ThreadingHTTPServer(("127.0.0.1", config["port"]), Handler)
def guard():
    while os.read(0, 64): pass
    server.shutdown()
threading.Thread(target=guard, daemon=True).start()
server.serve_forever(poll_interval=0.1)
