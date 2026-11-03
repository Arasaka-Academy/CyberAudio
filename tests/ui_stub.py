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
            self.respond({"status": "ready", "busy": False, "instance": args.instance, "startup": {"load_ms": 1, "warmup_ms": 1}})

    def do_POST(self):
        payload = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path.endswith("transcriptions"):
            self.respond({"text": "这是 CyberAudio 的界面测试结果。", "language": "zh", "duration": 1, "processing_ms": 12})
        else:
            value = json.loads(payload)
            self.respond(pcm if value.get("stream") else wav,
                {"Content-Type": "application/octet-stream" if value.get("stream") else "audio/wav", "X-Request-ID": "ui-test", "X-Audio-Sample-Rate": "24000"})


server = ThreadingHTTPServer(("127.0.0.1", config["port"]), Handler)
def guard():
    while os.read(0, 64): pass
    server.shutdown()
threading.Thread(target=guard, daemon=True).start()
server.serve_forever(poll_interval=0.1)
