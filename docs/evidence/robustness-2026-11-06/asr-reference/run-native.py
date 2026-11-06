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
plan = json.loads((base / "plan.json").read_text())
previous = json.loads((base.parent / "power-kernel-20261107/verification.json").read_text())
assert previous["status"] == "verified" and previous["all_owned_processes_absent"]
root = base / "native-http"
assert not root.exists()
app = project / ".context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app"
helper = app / "Contents/Helpers/CyberAudioService"
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert sha(helper) == "46083a4f7ba269b7f28377bd9532ede44ac0a812a55a55d1e1f0a9bd225cf86d"
port = 18892
with socket.socket() as probe:
    assert probe.connect_ex(("127.0.0.1", port)) != 0
process = None
token = ""
preserved = None
report = {"status": "running", "started": datetime.now(timezone.utc).isoformat(),
          "scope": "Current final-App auto-ASR on all twelve fixed prior bilingual/control inputs; no synthetic regeneration or parameter changes",
          "plan_sha256": sha(base / "plan.json"), "helper_sha256": sha(helper), "port": port, "results": []}
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
    manifest = [dict(row, source_id=row["id"], part=0, language="auto") for row in plan["cases"]]
    for item in manifest:
        path = Path(item["audio"])
        assert sha(path) == item["audio_sha256"]
        with wave.open(str(path), "rb") as reader:
            assert (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) == (1, 2, 16000)
    (root / "corpus.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    report["expected_requests"] = len(manifest)
    report["expected_source_outputs"] = len(plan["cases"])
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
            assert time.monotonic() - began < 600, "Roundtrip time budget reached"
            path = Path(row["audio"])
            assert sha(path) == row["audio_sha256"]
            boundary = "diagnostic-" + uuid.uuid4().hex
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\n{row["language"]}\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n\r\n').encode()
            body += path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
            status, result = fetch("/v1/audio/transcriptions", body, "multipart/form-data; boundary=" + boundary)
            report["results"].append({"id": row["id"], "source_id": row["source_id"], "part": row["part"], "asr_language": row["language"],
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
    try:
        report["production_after"] = production_snapshot()
        assert report["production_after"] == report["production_before"]
        if preserved is not None:
            assert preserved == {name: sha(root / "config" / name) for name in preserved}
        report["production_and_config_preserved"] = True
    except Exception as preservation_error:
        report["status"] = "failed"
        report["preservation_error"] = str(preservation_error)
    report["finished"] = datetime.now(timezone.utc).isoformat()
    if (root / "reports").exists():
        save()
raise SystemExit(0 if report["status"] == "diagnostic_completed" else 1)
