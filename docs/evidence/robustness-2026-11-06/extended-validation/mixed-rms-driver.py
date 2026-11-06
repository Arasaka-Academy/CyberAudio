"""Controlled names/numbers/noise cohort; preserve raw old/new model results.

Composite speech and added Gaussian noise are controlled transformations of
public human recordings, not natural code-switch or microphone acceptance.
"""
from datetime import datetime, timezone
from pathlib import Path
import array
import hashlib
import json
import math
import os
import platform
import random
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave


base = Path(__file__).resolve().parent
project = base.parents[2]
root = base / "m1-mixed-rms-diagnostics"
assert not root.exists(), "Preserve previous cohort data"
corpus = base / "corpus"
assets = project / "dist/CyberAudio.app/Contents/Helpers/CyberAudioAssets"
sources = {v["id"]: v for v in json.loads((corpus / "corpus.json").read_text())}
selected = ["zh-002-1883", "zh-008-1805", "zh-014-1902", "zh-020-1779",
            "en-008-1846", "en-010-1776", "en-024-1705", "en-046-1710"]
decoded_sources = {}
models = {"asr_dir": str((project / ".context/robustness/linked-models/asr").resolve()),
          "tts_dir": str((project / ".context/robustness/linked-models/tts").resolve()),
          "ffmpeg": "/opt/homebrew/bin/ffmpeg"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def get(port, path, token=None, body=None, content_type="application/json"):
    headers = {"Authorization": "Bearer " + token} if token else {}
    if body is not None:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(f"http://127.0.0.1:{port}" + path, data=body, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=150)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


def read_source(identity):
    row = sources[identity]
    path = corpus / row["audio"]
    assert digest(path) == row["audio_sha256"]
    metadata = json.loads(subprocess.check_output([
        "/opt/homebrew/bin/ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
        "stream=codec_name,sample_fmt,sample_rate,channels", "-of", "json", str(path)], text=True))["streams"][0]
    assert metadata == {"codec_name": "pcm_f32le", "sample_fmt": "flt", "sample_rate": "16000", "channels": 1}
    with wave.open(str(path), "rb") as sound:
        assert sound.getnchannels() == 1 and sound.getframerate() == 16000 and sound.getsampwidth() == 4
        samples = array.array("f", sound.readframes(sound.getnframes()))
        assert samples.itemsize == 4
        if sys.byteorder != "little":
            samples.byteswap()
    canonical = array.array("f", subprocess.check_output([
        "/opt/homebrew/bin/ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
        "-f", "f32le", "-ar", "16000", "-ac", "1", "pipe:1"]))
    if sys.byteorder != "little":
        canonical.byteswap()
    assert len(samples) == len(canonical) and canonical
    assert all(math.isfinite(x) for x in canonical)
    maximum_error = max(abs(a-b) for a, b in zip(samples, canonical))
    assert maximum_error == 0, "Source float interpretation must exactly match FFmpeg"
    decoded_sources[identity] = {"format": metadata, "frames": len(canonical),
        "wave_float_vs_ffmpeg_maximum_error": maximum_error,
        "peak": max(abs(x) for x in canonical),
        "rms": math.sqrt(sum(x*x for x in canonical)/len(canonical))}
    return list(canonical)


def make_audio(identity, samples, reference, language, origin, snr):
    peak = max((abs(x) for x in samples), default=0)
    clean = [x * 0.35 / peak for x in samples] if peak else samples
    signal_rms = math.sqrt(sum(x*x for x in clean) / len(clean))
    seed = int(hashlib.sha256(("cyberaudio-controlled-cohort-v1/" + identity).encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    noise_rms = signal_rms / (10 ** (snr / 20)) if snr is not None else 0
    numbers = array.array("h")
    error_power = 0.0
    clipped = 0
    for value in clean:
        mixed = value + (rng.gauss(0, noise_rms) if noise_rms else 0)
        clipped += abs(mixed) > 1
        number = round(max(-1, min(32767/32768, mixed)) * 32768)
        numbers.append(number)
        error_power += (number/32768-value)**2
    assert not clipped
    if sys.byteorder != "little":
        numbers.byteswap()
    path = root / "audio" / (identity + ".wav")
    with wave.open(str(path), "wb") as sound:
        sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(16000); sound.writeframes(numbers.tobytes())
    decoded = array.array("f", subprocess.check_output([
        "/opt/homebrew/bin/ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
        "-f", "f32le", "-ar", "16000", "-ac", "1", "pipe:1"]))
    if sys.byteorder != "little":
        decoded.byteswap()
    pcm = array.array("h", numbers)
    if sys.byteorder != "little":
        pcm.byteswap()
    assert len(decoded) == len(clean)
    assert max(abs(a-b/32768) for a, b in zip(decoded, pcm)) == 0
    if snr is None:
        assert max(abs(a-b) for a, b in zip(decoded, clean)) <= 0.5/32768
    measured = 10*math.log10(signal_rms**2/(error_power/len(clean))) if snr is not None else None
    if snr is not None:
        assert abs(measured - snr) < 0.15
    return {"id": identity, "audio": path.name, "audio_sha256": digest(path), "reference": reference,
            "language": language, "source_ids": origin, "sample_rate": 16000, "sample_width": 2,
            "duration_seconds": len(clean)/16000, "target_snr_db": snr, "measured_snr_db": measured,
            "seed": seed, "clipped_samples": clipped}


root.mkdir(parents=True)
(root / "audio").mkdir()
manifest = []
equalization = {}
def equalized(identity):
    samples = read_source(identity)
    rms = math.sqrt(sum(x*x for x in samples)/len(samples))
    assert rms > 0
    factor = 0.04/rms
    values = [x*factor for x in samples]
    assert max(abs(x) for x in values) < 0.9
    equalization[identity] = {"source_rms": rms, "gain": factor, "target_rms": 0.04,
                              "result_peak": max(abs(x) for x in values)}
    return values
for left, right in [(selected[0], selected[4]), (selected[3], selected[7])]:
    for first, second in [(left, right), (right, left)]:
        samples = equalized(first) + [0.0] * 4000 + equalized(second)
        for name, snr in [("clean", None), ("snr20", 20)]:
            manifest.append(make_audio("rms-composite-" + first + "-" + second + "-" + name, samples,
                sources[first]["reference"] + " " + sources[second]["reference"], "composite", [first, second], snr))
assert len(manifest) == 8
write_json(root / "corpus.json", manifest)
write_json(root / "provenance.json", {"source": "google/fleurs@168de341b3db6859a9bac1c50a2ef5e3b47647e0",
    "license": "CC-BY-4.0", "attribution": "FLEURS, Google, Conneau et al., 2022", "python": platform.python_version(),
    "selection": "Two previous bilingual pairs, each in both orders and clean/20dB; candidate-only diagnostic, not a new regression cohort",
    "selected": [{k:sources[i][k] for k in ["id", "audio_sha256", "reference", "language", "source"]} for i in equalization],
    "rms_equalization": equalization,
    "source_decoding": decoded_sources,
    "transform": "FFmpeg float32 -> each source RMS=0.04 -> concatenate with 250ms silence -> whole-file peak=0.35 -> deterministic Gaussian noise -> PCM16; no clipping",
    "validation": "Source float interpretation exactly equals FFmpeg; all output PCM16 frames exactly equal FFmpeg decode; clean quantization error <= 0.5 LSB",
    "composite": "Join two human recordings with 250ms silence; not natural code-switch speech",
    "limits": "Controlled concatenation and equal RMS, not perceptual loudness or natural code-switch; no microphone, room noise, entity labels or human quality score"})
production_status, production = get(8765, "/health")
assert production_status == 200
for name, app, expected_hash, port in [
    ("candidate", project / "dist/CyberAudio.app", "cc556b65e263e16218db4315ed8585dccaede7b5f539be7bbd68a4b04e841a89", 18874),
]:
    helper = app / "Contents/Helpers/CyberAudioService"
    assert digest(helper) == expected_hash
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", port)) != 0
    data = root / name
    subprocess.run([str(assets), "prepare", "--root", str(data), "--port", str(port)],
                   check=True, stdout=subprocess.DEVNULL, timeout=30)
    configuration = json.loads((data / "config/service.json").read_text())
    configuration.update(models)
    (data / "config/service.json").write_text(json.dumps(configuration))
    token = (data / "config/api-token").read_text().strip()
    config_hash, token_hash = digest(data / "config/service.json"), digest(data / "config/api-token")
    instance = uuid.uuid4().hex
    started = time.monotonic()
    report = {"status": "running", "started": datetime.now(timezone.utc).isoformat(), "stage": name,
              "device": "Apple M1 Max", "os": platform.platform(), "helper_sha256": expected_hash,
              "corpus_sha256": digest(root / "corpus.json"), "instance": instance, "results": []}
    process = subprocess.Popen(["/usr/bin/sandbox-exec", "-f", str(app / "Contents/Resources/offline.sb"),
                                str(helper), "--root", str(data), "--parent-pid", str(os.getpid()),
                                "--instance", instance, "--metallib", str(app / "Contents/Resources/mlx.metallib")],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        until = time.monotonic() + 180
        while True:
            assert process.poll() is None and time.monotonic() < until
            try:
                status, ready = get(port, "/ready")
            except OSError:
                time.sleep(0.2); continue
            assert ready.get("status") != "failed"
            if status == 200 and ready.get("status") == "ready":
                assert ready["instance"] == instance
                break
            time.sleep(0.2)
        report["startup"] = ready
        for row in manifest:
            audio = (root / "audio" / row["audio"]).read_bytes()
            assert hashlib.sha256(audio).hexdigest() == row["audio_sha256"]
            boundary = "quality-" + uuid.uuid4().hex
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n\r\n'.encode()
                    + audio + f"\r\n--{boundary}--\r\n".encode())
            status, result = get(port, "/v1/audio/transcriptions", token, body, "multipart/form-data; boundary=" + boundary)
            assert status == 200, result
            if row["id"] == "silence-control":
                assert result["text"] == "" and result["no_speech"]
            report["results"].append({"id": row["id"], "repetition": 0, "audio_sha256": row["audio_sha256"],
                "reference": row["reference"], "language": row["language"], "text": result["text"],
                "milliseconds": result["processing_ms"], "response": result})
            write_json(data / "reports/quality.json", report)
            if len(report["results"]) % 10 == 0:
                print(name, len(report["results"]), "/", len(manifest), flush=True)
        assert get(8765, "/health")[1]["instance"] == production["instance"]
        assert digest(data / "config/service.json") == config_hash and digest(data / "config/api-token") == token_hash
        assert not list((data / "tmp").glob("request-*"))
        process.stdin.close()
        process.wait(timeout=12)
        assert process.returncode == 0
        report["production_preserved"] = True
        report["configuration_and_token_preserved"] = True
        report["temporary_request_cleanup"] = True
        report["owned_helper_exit_code"] = process.returncode
        report["status"] = "completed"
    except Exception as error:
        report["status"] = "failed"
        report["error_type"] = type(error).__name__
        report["error"] = str(error)
        raise
    finally:
        if process.poll() is None:
            process.stdin.close()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=12)
                report["forced_cleanup"] = True
        report["elapsed_seconds"] = time.monotonic() - started
        report["finished"] = datetime.now(timezone.utc).isoformat()
        write_json(data / "reports/quality.json", report)
