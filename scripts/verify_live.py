"""Run on the inference Mac. Records public fixtures and metrics, never the API token."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import time
import wave

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, default=Path.home() / "Library/Application Support/CyberAudio")
parser.add_argument("--fixtures", type=Path, required=True)
parser.add_argument("--base")
parser.add_argument("--stability-seconds", type=int, default=0)
args = parser.parse_args()
config = json.loads((args.root / "config/service.json").read_text())
base = args.base or f"http://127.0.0.1:{config['port']}"
token = (args.root / "config/api-token").read_text().strip()
client = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=160, trust_env=False)
report = {"base": base, "started": time.time(), "stt": [], "tts": [], "checks": {}}
destination = args.root / "reports"
destination.mkdir(exist_ok=True)


def transcribe(path, language="auto"):
    begin = time.monotonic()
    with path.open("rb") as source:
        response = client.post("/v1/audio/transcriptions", files={"file": (path.name, source, "application/octet-stream")}, data={"language": language})
    response.raise_for_status()
    return {"file": path.name, "wall_ms": round((time.monotonic() - begin) * 1000, 2), **response.json()}


def synthesize(text, voice, language, streaming=False, save_artifact=True):
    begin = time.monotonic()
    payload = {"model": "qwen3-tts-1.7b", "input": text, "voice": voice, "language": language,
               "stream": streaming, "response_format": "pcm" if streaming else "wav"}
    first, pieces, data, received = None, 0, bytearray(), []
    with client.stream("POST", "/v1/audio/speech", json=payload) as response:
        response.raise_for_status()
        request_id = response.headers["x-request-id"]
        for piece in response.iter_bytes():
            if first is None: first = (time.monotonic() - begin) * 1000
            pieces += 1
            data.extend(piece)
            received.append(round((time.monotonic() - begin) * 1000, 2))
    elapsed = (time.monotonic() - begin) * 1000
    state = client.get("/v1/requests/" + request_id).json()
    if state["status"] != "completed":
        raise RuntimeError("Generation did not complete")
    if streaming:
        assert len(data) % 2 == 0 and data
        duration = len(data) / 48000
    else:
        with wave.open(io.BytesIO(data)) as audio:
            assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (24000, 1, 2)
            duration = audio.getnframes() / 24000
    record = {"reference": text, "voice": voice, "language": language, "stream": streaming,
              "wall_ms": round(elapsed, 2), "first_received_ms": round(first, 2), "pieces": pieces,
              "received_at_ms": received, "duration": duration, "rtf": round(elapsed / 1000 / duration, 3), "state": state}
    # These are intentional public test artifacts, not retained user requests.
    if save_artifact:
        (destination / f"tts-{language}-{'stream.pcm' if streaming else 'wav.wav'}").write_bytes(data)
    return record


report["ready"] = client.get("/ready").json()
assert report["ready"]["status"] == "ready"
report["models"] = client.get("/v1/models").json()
for name in ["official-zh.wav", "synth-en.wav", "synth-mixed.wav", "synth-names.wav", "synth-numbers.wav", "silence.wav", "noise.wav"]:
    result = transcribe(args.fixtures / name)
    report["stt"].append(result)
    print(json.dumps({"stt": result}, ensure_ascii=False), flush=True)
for text, voice, language, stream in [
    ("你好，欢迎使用 CyberAudio。今天下午三点十五分，请提醒我开会。", "serena", "chinese", False),
    ("Welcome to CyberAudio. Please check the network connection on the Mac mini.", "ryan", "english", False),
    ("请检查 CyberAudio 的 API，端口是八七六五。", "vivian", "auto", True),
]:
    result = synthesize(text, voice, language, stream)
    report["tts"].append(result)
    print(json.dumps({"tts": result}, ensure_ascii=False), flush=True)
    if not stream:
        path = destination / f"tts-{language}-wav.wav"
        result["asr_roundtrip"] = transcribe(path)["text"]

assert client.get("/v1/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
assert client.post("/v1/audio/speech", json={"input": "x" * 501}).status_code == 400
with ThreadPoolExecutor(max_workers=1) as pool:
    pending = pool.submit(synthesize, "这是并发互斥测试，请等待这句话朗读完成。", "serena", "chinese")
    for _ in range(200):
        if client.get("/ready").json()["busy"]: break
        time.sleep(0.02)
    assert client.post("/v1/audio/speech", json={"input": "第二个请求"}).status_code == 429
    assert client.post("/v1/audio/transcriptions", files={"file": ("test.wav", (args.fixtures / "official-zh.wav").read_bytes())}).status_code == 429
    pending.result()
report["checks"] = {"auth": True, "input_limit": True, "cross_model_busy": True, "temporary_audio_empty": not list((args.root / "tmp").glob("request-*"))}
latencies = []
for _ in range(10):
    for name in ["synth-zh-10s.wav", "synth-en-10s.wav"]:
        result = transcribe(args.fixtures / name)
        latencies.append(result["wall_ms"])
latencies.sort()
report["stt_short_latency"] = {"count": len(latencies), "p50_ms": latencies[len(latencies) // 2], "p95_ms": latencies[int(len(latencies) * .95) - 1], "samples_ms": latencies}
(destination / "live-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
print("LIVE_VERIFICATION_COMPLETE", flush=True)

if args.stability_seconds:
    begin = time.monotonic()
    stability = {"started": time.time(), "requested_seconds": args.stability_seconds, "samples": [], "errors": []}
    index = 0
    while time.monotonic() - begin < args.stability_seconds:
        index += 1
        try:
            result = synthesize("CyberAudio 本地语音服务运行正常。", "serena", "chinese", index % 2 == 0, save_artifact=False)
            result.pop("received_at_ms", None)
            result.pop("reference", None)
            result["stt"] = transcribe(args.fixtures / "official-zh.wav")
            result["at_seconds"] = round(time.monotonic() - begin, 1)
            stability["samples"].append(result)
        except Exception as exc:
            stability["errors"].append({"at_seconds": round(time.monotonic() - begin, 1), "type": type(exc).__name__})
        stability["elapsed_seconds"] = round(time.monotonic() - begin, 1)
        (destination / "stability.json").write_text(json.dumps(stability, ensure_ascii=False, indent=2))
        print(json.dumps({"stability_seconds": stability["elapsed_seconds"], "cycles": index, "errors": len(stability["errors"])}), flush=True)
        time.sleep(min(20, max(0, args.stability_seconds - (time.monotonic() - begin))))
    stability["elapsed_seconds"] = round(time.monotonic() - begin, 1)
    stability["finished"] = time.time()
    (destination / "stability.json").write_text(json.dumps(stability, ensure_ascii=False, indent=2))
    print("STABILITY_COMPLETE", flush=True)
