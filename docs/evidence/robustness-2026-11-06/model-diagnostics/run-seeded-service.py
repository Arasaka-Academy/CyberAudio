"""Bounded real-HTTP reproduction of the voice/text from failed M4 cycle 708.

This is a diagnostic, not a replacement two-hour acceptance or listening test.
"""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import http.client
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid

base = Path(__file__).resolve().parent
project = base.parents[2]
root = base / "m1-seeded-tts-service"
assert not root.exists()
app = project / "dist/CyberAudio.app"
helper = base / "seeded-service-build/CyberAudioSeededService"
assets = app / "Contents/Helpers/CyberAudioAssets"
expected_hash = json.loads((base / "seeded-service-build/build.json").read_text())["executable_sha256"]
assert hashlib.sha256(helper.read_bytes()).hexdigest() == expected_hash
port = 18880
process = None
token = ""
began = time.monotonic()
report = {"status": "running", "started": datetime.now(timezone.utc).isoformat(),
          "helper_sha256": expected_hash, "device": "Apple M1 Max", "port": port,
          "scope": "Private fixed-seed-196 Helper; unchanged sampling parameters, HTTP and coordinator. Does not identify original M4 terminal cause.",
          "maximum_requests": 1, "maximum_seconds": 900, "results": []}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save():
    report["elapsed_seconds"] = time.monotonic()-began
    path = root / "reports/tts-failure-probe.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    temporary.replace(path)


def fetch(path, body=None, external=False, headers=None):
    address = "http://127.0.0.1:"+str(8765 if external else port)+path
    fields = {} if external else {"Authorization": "Bearer "+token}
    fields.update(headers or {})
    if body is not None:
        fields["Content-Type"] = "application/json"
        body = json.dumps(body).encode()
    request = urllib.request.Request(address, data=body, headers=fields)
    try:
        response = urllib.request.urlopen(request, timeout=150)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        fields = response.headers
        try:
            data = response.read()
        except http.client.IncompleteRead as error:
            data = error.partial
            fields["X-Validation-Read-Error"] = "IncompleteRead"
        return response.status, data, fields


def value(path, external=False):
    status, body, _ = fetch(path, external=external)
    return status, json.loads(body)


def speech(text, voice="ono_anna", streaming=True):
    started = time.monotonic()
    status, data, headers = fetch("/v1/audio/speech", {"input": text, "voice": voice,
        "language": "chinese", "response_format": "pcm" if streaming else "wav", "stream": streaming})
    identity = headers.get("X-Request-ID")
    assert identity
    record_status, record = value("/v1/requests/"+identity)
    assert record_status == 200
    return {"http_status": status, "http_ms": (time.monotonic()-started)*1000,
            "transport_error": headers.get("X-Validation-Read-Error"),
            "bytes": len(data), "output_seconds": (len(data)-(0 if streaming else 44))/48000,
            "record": record, "output_sha256": hashlib.sha256(data).hexdigest()}


try:
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", port)) != 0
    status, production = value("/health", external=True)
    assert status == 200
    report["production_before"] = production["instance"]
    production_config = Path.home()/"Library/Application Support/CyberAudio/config"
    production_hashes = {name: digest(production_config/name) for name in ["service.json", "api-token"]}
    report["production_hashes_before"] = production_hashes
    subprocess.run([str(assets), "prepare", "--root", str(root), "--port", str(port)],
                   check=True, stdout=subprocess.DEVNULL, timeout=30)
    configuration = json.loads((root / "config/service.json").read_text())
    configuration.update(asr_dir=str((project/".context/robustness/linked-models/asr").resolve()),
        tts_dir=str((project/".context/robustness/linked-models/tts").resolve()), ffmpeg="/opt/homebrew/bin/ffmpeg")
    (root / "config/service.json").write_text(json.dumps(configuration))
    token = (root/"config/api-token").read_text().strip()
    config_hash, token_hash = digest(root/"config/service.json"), digest(root/"config/api-token")
    report["instance"] = uuid.uuid4().hex
    process = subprocess.Popen(["/usr/bin/sandbox-exec", "-f", str(app/"Contents/Resources/offline.sb"),
        str(helper), "--root", str(root), "--parent-pid", str(os.getpid()),
        "--instance", report["instance"], "--metallib", str(app/"Contents/Resources/mlx.metallib")],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    until = time.monotonic()+180
    while True:
        assert process.poll() is None and time.monotonic() < until
        try:
            status, ready = value("/ready")
        except OSError:
            time.sleep(0.2); continue
        assert ready.get("status") != "failed"
        if status == 200 and ready.get("status") == "ready":
            assert ready["instance"] == report["instance"]
            report["startup"] = ready
            break
        time.sleep(0.2)
    for number in range(1, 2):
        assert time.monotonic()-began < 900, "Bounded reproduction budget reached"
        result = speech("持续验证语音服务的稳定性和恢复能力。")
        result["number"] = number
        report["results"].append(result)
        save()
        if result["record"]["status"] != "completed":
            report["failure_observed"] = result
            print("REQUEST FAILURE", json.dumps(result, ensure_ascii=False), flush=True)
            break
        assert result["transport_error"] is None
        if number % 10 == 0:
            print("COMPLETED", number, flush=True)
    report["quality_observation"] = "request_failure_reproduced" if "failure_observed" in report else "not_reproduced_within_1_request"
    assert report["failure_observed"]["record"]["code"] == "generation_limit"
    status, ready = value("/ready")
    assert status == 200 and ready["instance"] == report["instance"] and not ready["busy"]
    recovery = speech("生成之后恢复正常。", voice="serena", streaming=False)
    assert recovery["http_status"] == 200 and recovery["record"]["status"] == "completed" and recovery["bytes"] > 44
    report["same_instance_recovery"] = recovery
    lease = {"x-cyberaudio-instance": report["instance"], "x-cyberaudio-update": uuid.uuid4().hex}
    status, data, _ = fetch("/internal/update/prepare", {}, headers=lease)
    assert status == 200
    status, _, _ = fetch("/v1/audio/speech", {"input": "暂停期间的新请求"})
    assert status == 503
    status, _, _ = fetch("/internal/update/cancel", {}, headers=lease)
    assert status == 200
    report["update_pause_resume"] = True
    assert not list((root/"tmp").glob("request-*"))
    report["temporary_request_cleanup"] = True
    process.stdin.close()
    process.wait(timeout=12)
    assert process.returncode == 0
    report["owned_helper_exit_code"] = process.returncode
    assert value("/health", external=True)[1]["instance"] == production["instance"]
    assert digest(root/"config/service.json") == config_hash and digest(root/"config/api-token") == token_hash
    report["production_and_isolated_config_token_preserved"] = True
    assert production_hashes == {name: digest(production_config/name) for name in production_hashes}
    report["production_config_token_preserved"] = True
    report["status"] = "completed"
except Exception as error:
    report["status"] = "failed"
    report["error_type"] = type(error).__name__
    report["error"] = str(error)
    raise
finally:
    if process is not None and process.poll() is None:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=12)
            report["forced_cleanup"] = True
    report["finished"] = datetime.now(timezone.utc).isoformat()
    if (root/"reports").exists():
        save()
