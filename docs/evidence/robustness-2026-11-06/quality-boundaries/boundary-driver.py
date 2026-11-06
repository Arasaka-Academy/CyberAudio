"""Actual-model boundary acceptance in a new owned M1 data directory."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import io
import json
import os
import re
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import wave


base = Path(__file__).resolve().parent
project = base.parents[2]
root = base / "m1-boundary-acceptance"
assert not root.exists(), "Preserve previous acceptance data"
port = 18871
url = "http://127.0.0.1:" + str(port)
app = project / "dist/CyberAudio.app"
helper = app / "Contents/Helpers/CyberAudioService"
assets = helper.with_name("CyberAudioAssets")
corpus = base / "corpus"
began = time.monotonic()
process = None
token = ""
report = {"status": "running", "started": datetime.now(timezone.utc).isoformat(),
          "runtime_fix_commit": "e2f34df30eab49e526a3dfd2fa7d6fffbfb81d0b",
          "device": "Apple M1 Max", "port": port, "checks": {},
          "limitations": "Actual models and controlled audio inputs; no physical microphone or human listening acceptance"}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def save():
    report["elapsed_seconds"] = time.monotonic() - began
    temporary = root / "reports/boundaries.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(root / "reports/boundaries.json")


def fetch(path, body=None, content_type="application/json", external=False):
    headers = {} if external else {"Authorization": "Bearer " + token}
    if body is not None:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(("http://127.0.0.1:8765" if external else url) + path,
                                     data=body, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=150)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        data = response.read()
        return response.status, data, response.headers


def value(path, external=False):
    status, data, headers = fetch(path, external=external)
    return status, json.loads(data)


def completed_record(offset):
    text = (root / "logs/service.log").read_text()[offset:]
    matches = re.findall(r"request_done id=([a-f0-9]+) status=([a-z]+)", text)
    assert matches, "No actual request completion in the owned service log"
    identity, state = matches[-1]
    status, record = value("/v1/requests/" + identity)
    assert status == 200 and record["status"] == state
    return record


def checkpoint(name, result):
    report["checks"][name] = result
    save()
    print("PASS", name, flush=True)


def multipart(audio):
    boundary = "actual-boundary-" + uuid.uuid4().hex
    data = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n\r\n'.encode()
            + audio + f"\r\n--{boundary}--\r\n".encode())
    return data, "multipart/form-data; boundary=" + boundary


def transcribe(audio):
    body, content_type = multipart(audio)
    status, data, headers = fetch("/v1/audio/transcriptions", body, content_type)
    return status, json.loads(data)


def wait_idle(seconds=15):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        status, ready = value("/ready")
        assert status == 200 and ready["instance"] == report["instance"]
        if not ready["busy"]:
            return
        time.sleep(0.01)
    raise AssertionError("Actual producer did not drain")


def disconnect(path, body, content_type):
    offset = len((root / "logs/service.log").read_text())
    stream = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        head = (f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {token}\r\n"
                f"Content-Type: {content_type}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode()
        stream.sendall(head + body)
        until = time.monotonic() + 5
        while True:
            status, ready = value("/ready")
            assert status == 200 and ready["instance"] == report["instance"]
            if ready["busy"]:
                break
            assert time.monotonic() < until, "Request was never observed in inference"
            time.sleep(0.01)
        # Wait beyond the admission transition to overlap decoder/prefill or
        # token work. Refuse an inconclusive cancellation after completion.
        time.sleep(0.15)
        assert value("/ready")[1]["busy"], "Request finished before disconnect; cancellation not proven"
        stream.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        cancel_time = time.monotonic()
        stream.close()
        wait_idle()
        latency = (time.monotonic() - cancel_time) * 1000
        record = completed_record(offset)
        assert record["status"] == "cancelled", record
        return {"request": record, "disconnect_to_idle_ms": latency,
                "observed_busy_before_disconnect": True, "cancelled_during_active_work": True}
    finally:
        stream.close()


def inspect_wav(data):
    with wave.open(io.BytesIO(data), "rb") as sound:
        assert sound.getnchannels() == 1 and sound.getframerate() == 24000 and sound.getsampwidth() == 2
        assert sound.getnframes() > 0
        frames = sound.getnframes()
        pcm = sound.readframes(frames)
        assert len(pcm) == frames * 2
    return {"frames": frames, "duration_seconds": frames / 24000,
            "channels": 1, "sample_rate": 24000, "sample_width": 2}


try:
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", port)) != 0, "Isolated test port occupied"
    report["helper_sha256"] = digest(helper)
    assert report["helper_sha256"] == "cc556b65e263e16218db4315ed8585dccaede7b5f539be7bbd68a4b04e841a89"
    production_status, production = value("/health", external=True)
    assert production_status == 200
    report["production_before"] = production["instance"]
    subprocess.run([str(assets), "prepare", "--root", str(root), "--port", str(port)],
                   check=True, stdout=subprocess.DEVNULL, timeout=30)
    configuration = json.loads((root / "config/service.json").read_text())
    configuration.update(asr_dir=str((project / ".context/robustness/linked-models/asr").resolve()),
                         tts_dir=str((project / ".context/robustness/linked-models/tts").resolve()),
                         ffmpeg="/opt/homebrew/bin/ffmpeg")
    (root / "config/service.json").write_text(json.dumps(configuration))
    token = (root / "config/api-token").read_text().strip()
    config_hash = digest(root / "config/service.json")
    token_hash = digest(root / "config/api-token")
    report["physical_memory_bytes"] = int(subprocess.check_output(["/usr/sbin/sysctl", "-n", "hw.memsize"]))
    report["os"] = subprocess.check_output(["/usr/bin/sw_vers", "-productVersion"], text=True).strip()
    report["instance"] = uuid.uuid4().hex
    process = subprocess.Popen(["/usr/bin/sandbox-exec", "-f", str(app / "Contents/Resources/offline.sb"),
                                str(helper), "--root", str(root), "--parent-pid", str(os.getpid()),
                                "--instance", report["instance"], "--metallib", str(app / "Contents/Resources/mlx.metallib")],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    until = time.monotonic() + 180
    while True:
        assert process.poll() is None and time.monotonic() < until, "Owned model initialization failed"
        try:
            status, ready = value("/ready")
        except OSError:
            time.sleep(0.2)
            continue
        assert ready.get("status") != "failed", "Actual model initialization failed"
        if status == 200 and ready.get("status") == "ready":
            break
        time.sleep(0.2)
    checkpoint("startup", ready)
    rows = json.loads((corpus / "corpus.json").read_text())
    source = corpus / rows[0]["audio"]
    assert digest(source) == rows[0]["audio_sha256"]
    (root / "audio").mkdir()
    for duration in (60, 60.1):
        path = root / "audio" / ("human-loop-" + str(duration) + ".wav")
        subprocess.run(["/opt/homebrew/bin/ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                        "-stream_loop", "-1", "-i", str(source), "-t", str(duration), "-ar", "16000", "-ac", "1",
                        "-c:a", "pcm_s16le", "-y", str(path)], check=True, timeout=30)
        status, result = transcribe(path.read_bytes())
        if duration == 60:
            assert status == 200 and result.get("text") and abs(result["duration"] - 60) < 0.001
        else:
            assert status == 413 and result["error"]["code"] == "audio_too_long"
        checkpoint("asr_" + str(duration) + "_seconds", {"http_status": status, "response": result,
            "audio_sha256": digest(path), "construction": "Loop fixed public FLEURS sample and trim; controlled duration input"})
    body, content_type = multipart((root / "audio/human-loop-60.wav").read_bytes())
    cancellation = disconnect("/v1/audio/transcriptions", body, content_type)
    status, recovery = transcribe(source.read_bytes())
    assert status == 200 and recovery.get("text")
    cancellation["recovery"] = recovery
    checkpoint("actual_http_asr_cancellation_and_recovery", cancellation)

    long_english = ("Today we are checking the local speech service with a longer paragraph. "
        "The meeting starts at nine thirty tomorrow morning, and the order number is one two three four five six. "
        "Please read every sentence clearly, leave a short pause between ideas, and continue until the final words. "
        "The application should keep the complete audio available for playback and export. "
        "This final sentence marks the end of the paragraph.")
    paragraph = "今天我们检查本地语音系统在较长段落中的表现。每句话都应该清楚自然，数字按顺序朗读，句子之间保留适当停顿。完成以后，可以重播和导出音频。"
    cases = [
        ("long-english", long_english, "ryan", "english", "Read naturally and clearly."),
        ("maximum-500-scalars", (paragraph * 20)[:500], "serena", "chinese", "自然清晰地朗读。"),
        ("controlled-style", "请在明天上午九点半提醒我开会。订单号是一二三四五六，温度是二十三点五度。", "vivian", "chinese", "语气温和，放慢速度，数字清楚。"),
    ]
    for name, text, voice, language, instructions in cases:
        assert len(text) <= 500
        offset = len((root / "logs/service.log").read_text())
        start = time.monotonic()
        status, audio, headers = fetch("/v1/audio/speech", json.dumps({"input": text, "voice": voice,
            "language": language, "instructions": instructions, "response_format": "wav"}).encode())
        result = {"input": text, "unicode_scalars": len(text), "voice": voice, "instructions": instructions,
                  "http_status": status, "http_ms": (time.monotonic() - start) * 1000}
        record = completed_record(offset)
        result["request"] = record
        if status == 422 and name == "maximum-500-scalars":
            failure = json.loads(audio)
            assert failure["error"]["code"] == "generation_limit"
            assert record["status"] == "failed" and record["code"] == "generation_limit"
            result["generation_limit"] = failure
        else:
            assert status == 200 and record["status"] == "completed"
            result["wav"] = inspect_wav(audio)
            assert result["wav"]["duration_seconds"] <= 100
            path = root / "audio" / (name + ".wav")
            path.write_bytes(audio)
            result["audio_sha256"] = digest(path)
            if result["wav"]["duration_seconds"] <= 60:
                back_status, back_result = transcribe(audio)
                assert back_status == 200 and back_result.get("text")
                result["synthetic_roundtrip_transcription"] = back_result
                result["roundtrip_scope"] = "Content observation only; not human pronunciation or naturalness acceptance"
        wait_idle()
        checkpoint(name, result)
    cancel_text = (paragraph * 20)[:500]
    cancellation = disconnect("/v1/audio/speech", json.dumps({"input": cancel_text, "voice": "serena",
        "language": "chinese", "response_format": "wav"}).encode(), "application/json")
    status, audio, headers = fetch("/v1/audio/speech", json.dumps({"input": "取消以后恢复正常。", "response_format": "wav"}).encode())
    assert status == 200
    cancellation["recovery_wav"] = inspect_wav(audio)
    checkpoint("actual_nonstreaming_tts_cancel_and_recovery", cancellation)
    wait_idle()
    assert not list((root / "tmp").glob("request-*"))
    checkpoint("temporary_request_cleanup", True)
    eof = time.monotonic()
    process.stdin.close()
    process.wait(timeout=12)
    assert process.returncode == 0
    checkpoint("stdin_eof", {"exit_code": process.returncode, "stop_ms": (time.monotonic() - eof) * 1000})
    assert value("/health", external=True)[1]["instance"] == report["production_before"]
    assert digest(root / "config/service.json") == config_hash and digest(root / "config/api-token") == token_hash
    report["production_preserved"] = True
    report["isolated_configuration_and_token_preserved"] = True
    report["status"] = "passed"
except Exception as error:
    report["status"] = "failed"
    report["error_type"] = type(error).__name__
    report["error"] = str(error)
    raise
finally:
    if process is not None and process.poll() is None:
        process.stdin.close()
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=12)
            report["forced_cleanup"] = True
    report["finished"] = datetime.now(timezone.utc).isoformat()
    if (root / "reports").is_dir():
        save()
