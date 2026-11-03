"""Client-side HTTPS acceptance. Set CYBERAUDIO_TOKEN; never writes credentials."""
import argparse
import io
import json
import os
from pathlib import Path
import time
import wave

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--base", required=True)
parser.add_argument("--audio", type=Path, required=True)
parser.add_argument("--report", type=Path, required=True)
args = parser.parse_args()
token = os.environ["CYBERAUDIO_TOKEN"]
client = httpx.Client(base_url=args.base, headers={"Authorization": f"Bearer {token}"}, timeout=45, trust_env=False)
report = {"base": args.base, "started": time.time()}


def wait_idle():
    for _ in range(300):
        value = client.get("/ready").json()
        if value["status"] == "ready" and not value["busy"]: return
        time.sleep(0.1)
    raise RuntimeError("Server remained busy")


models = client.get("/v1/models")
models.raise_for_status()
assert len(models.json()["data"]) == 2
report["models"] = models.json()
assert client.get("/v1/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
wait_idle()
with args.audio.open("rb") as source:
    response = client.post("/v1/audio/transcriptions", files={"file": ("speech.wav", source)}, data={"language": "zh"})
response.raise_for_status()
report["transcription"] = response.json()
assert report["transcription"]["text"]
wait_idle()
response = client.post("/v1/audio/speech", json={"input": "CyberAudio 的统一语音接口已经准备好了。", "voice": "serena", "response_format": "wav"})
response.raise_for_status()
with wave.open(io.BytesIO(response.content)) as audio:
    assert (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()) == (1, 24000, 2)
    report["wav_duration"] = audio.getnframes() / audio.getframerate()
wait_idle()
start = time.monotonic()
with client.stream("POST", "/v1/audio/speech", json={"input": "这是断开连接测试。" * 12, "voice": "serena", "response_format": "pcm", "stream": True}) as response:
    response.raise_for_status()
    request_id = response.headers["x-request-id"]
    for chunk in response.iter_bytes():
        assert chunk
        report["disconnect_first_audio_ms"] = round((time.monotonic() - start) * 1000, 2)
        break
# Closing the network response must cancel production; it must not free the slot too early.
wait_idle()
record = client.get("/v1/requests/" + request_id).json()
assert record["status"] == "cancelled", record
report["disconnect_status"] = record
report["disconnect_recovered_ms"] = round((time.monotonic() - start) * 1000, 2)
report["finished"] = time.time()
args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({"https": "passed", "stt": True, "wav": True, "stream_disconnect": record["status"], "first_audio_ms": report["disconnect_first_audio_ms"], "recovered_ms": report["disconnect_recovered_ms"]}))
