"""Transcribe every predeclared matrix output after the TTS process terminates."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import wave

base = Path(__file__).resolve().parent
project = base.parents[2]
receipt = json.loads((base / "subtalker-matrix-result.json").read_text())
assert receipt["status"] == "completed" and receipt["exit_code"] == 0 and receipt["finished"]
assert receipt["production_preserved"] and receipt["sdk_and_helper_preserved"]
identity = subprocess.run(["/bin/ps", "-p", str(receipt["owned_group_pid"]), "-o", "pid=,lstart=,command="], capture_output=True, text=True).stdout
assert "subtalker-matrix-probe" not in identity and "sandbox-exec" not in identity
matrix_root = base / "m1-subtalker-matrix"
matrix = json.loads((matrix_root / "report.json").read_text())
assert matrix["status"] == "diagnostic_completed" and matrix["completed_generations"] == 144
assert matrix["preliminary_failure_parity_sha256"] == receipt["build"]["preliminary_report_sha256"]
baseline_root = base / "m1-tts-matrix-r2"
baseline_path = baseline_root / "report.json"
assert hashlib.sha256(baseline_path.read_bytes()).hexdigest() == receipt["build"]["baseline_report_sha256"]
baseline = json.loads(baseline_path.read_text())
assert baseline["status"] == "diagnostic_completed" and baseline["completed_generations"] == 288
baseline_rows = [row for row in baseline["results"] if row["temperature"] == 0.7]
assert len(baseline_rows) == 144
candidate_rows = matrix["results"]
assert len({(r["text_id"], r["voice"], r["seed"]) for r in candidate_rows}) == 144
assert {(r["text_id"], r["voice"], r["seed"]) for r in baseline_rows} == {(r["text_id"], r["voice"], r["seed"]) for r in candidate_rows}
matrix["results"] = [dict(row, id="baseline__" + row["id"], source_group="baseline", pcm_source=str(baseline_root / (row["id"] + ".pcm"))) for row in baseline_rows] + [dict(row, id="candidate__" + row["id"], source_group="candidate", pcm_source=str(matrix_root / (row["id"] + ".pcm"))) for row in candidate_rows]
root = base / "m1-subtalker-matrix-roundtrip"
assert not root.exists()
app = base / "final-bytes-small-range/CyberAudio.app"
helper = app / "Contents/Helpers/CyberAudioService"
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert sha(helper) == "e9a9c159eb56150b07a59729c70e73b021282c07fc2331dcb468e8cdd98ad30e"
port = 18884
with socket.socket() as probe:
    assert probe.connect_ex(("127.0.0.1", port)) != 0
process = None
token = ""
preserved = None
report = {"status": "running", "started": datetime.now(timezone.utc).isoformat(),
          "scope": "All 144 independent-subtalker candidates and retained 144 original baselines through the same final-App ASR; no listening acceptance",
          "matrix_report_sha256": sha(matrix_root / "report.json"), "baseline_matrix_report_sha256": sha(baseline_path), "plan_sha256": matrix["plan_sha256"],
          "helper_sha256": sha(helper), "port": port, "results": []}
began = time.monotonic()


def save():
    path = root / "reports/asr-followup.json"
    report["elapsed_seconds"] = time.monotonic() - began
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def fetch(path, body=None, content_type=None, production=False):
    headers = {} if production else {"Authorization": "Bearer " + token}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(f"http://127.0.0.1:{8765 if production else port}" + path, data=body, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=150)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


def production_snapshot():
    state = fetch("/health", production=True)[1]
    data = Path.home() / "Library/Application Support/CyberAudio"
    return {"instance": state["instance"], "config_sha256": sha(data / "config/service.json"),
            "token_sha256": sha(data / "config/api-token")}


try:
    report["production_before"] = production_snapshot()
    subprocess.run([str(helper.with_name("CyberAudioAssets")), "prepare", "--root", str(root), "--port", str(port)],
                   check=True, stdout=subprocess.DEVNULL, timeout=30)
    (root / "audio").mkdir()
    configuration = json.loads((root / "config/service.json").read_text())
    configuration.update(asr_dir=str((project / ".context/robustness/linked-models/asr").resolve()),
                         tts_dir=str((project / ".context/robustness/linked-models/tts").resolve()), ffmpeg="/opt/homebrew/bin/ffmpeg")
    (root / "config/service.json").write_text(json.dumps(configuration))
    token = (root / "config/api-token").read_text().strip()
    preserved = {name: sha(root / "config" / name) for name in ["service.json", "api-token"]}
    manifest = []
    for row in matrix["results"]:
        raw = Path(row["pcm_source"])
        assert sha(raw) == row["pcm_sha256"]
        audio = raw.read_bytes()
        assert audio and len(audio) % 2 == 0 and len(audio) <= 96 * 48000
        for index, start in enumerate(range(0, len(audio), 24 * 48000)):
            part = audio[start:start + 24 * 48000]
            path = root / "audio" / (row["id"] + f"-part{index}.wav")
            with wave.open(str(path), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(24000)
                writer.writeframes(part)
            manifest.append({"id": path.stem, "source_id": row["id"], "part": index,
                "audio": str(path), "audio_sha256": sha(path), "source_pcm_sha256": row["pcm_sha256"],
                "language": "zh" if row["language"] == "chinese" else "en",
                "source_group": row["source_group"], "text_id": row["text_id"], "voice": row["voice"], "seed": row["seed"],
                "start_seconds": start / 48000, "duration_seconds": len(part) / 48000})
    (root / "corpus.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    report["expected_requests"] = len(manifest)
    report["expected_source_outputs"] = 288
    save()
    instance = uuid.uuid4().hex
    with (root / "helper.log").open("xb") as log:
        process = subprocess.Popen(["/usr/bin/sandbox-exec", "-f", str(app / "Contents/Resources/offline.sb"),
            str(helper), "--root", str(root), "--parent-pid", str(os.getpid()), "--instance", instance,
            "--metallib", str(app / "Contents/Resources/mlx.metallib")], stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
        report["owned_helper_pid"] = process.pid
        save()
        until = time.monotonic() + 180
        while True:
            assert process.poll() is None and time.monotonic() < until, "ASR helper startup failed or timed out"
            try:
                status, ready = fetch("/ready")
            except OSError:
                time.sleep(.2)
                continue
            assert ready["status"] != "failed"
            if status == 200 and ready["status"] == "ready":
                assert ready["instance"] == instance
                report["startup"] = ready
                break
            time.sleep(.2)
        for row in manifest:
            assert time.monotonic() - began < 1200, "Roundtrip time budget reached"
            path = Path(row["audio"])
            assert sha(path) == row["audio_sha256"]
            boundary = "diagnostic-" + uuid.uuid4().hex
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\n{row["language"]}\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n\r\n').encode()
            body += path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
            status, result = fetch("/v1/audio/transcriptions", body, "multipart/form-data; boundary=" + boundary)
            report["results"].append({"id": row["id"], "source_id": row["source_id"], "part": row["part"],
                                      "http_status": status, "response": result})
            save()
            assert status == 200, "ASR request failed; response retained in report"
            if len(report["results"]) % 25 == 0:
                print("Transcribed", len(report["results"]), "of", len(manifest), flush=True)
        assert not list((root / "tmp").glob("request-*"))
        process.stdin.close()
        process.wait(timeout=12)
        assert process.returncode == 0
    report["owned_helper_exit_code"] = process.returncode
    report["temporary_request_cleanup"] = True
    report["production_after"] = production_snapshot()
    assert report["production_after"] == report["production_before"]
    assert preserved == {name: sha(root / "config" / name) for name in preserved}
    report["production_and_config_preserved"] = True
    report["status"] = "diagnostic_completed"
except Exception as error:
    report["status"] = "failed"
    report["error_type"] = type(error).__name__
    report["error"] = str(error)
finally:
    if process is not None and process.poll() is None:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=12)
            report["forced_cleanup"] = True
    report["finished"] = datetime.now(timezone.utc).isoformat()
    if (root / "reports").exists():
        save()
raise SystemExit(0 if report["status"] == "diagnostic_completed" else 1)
