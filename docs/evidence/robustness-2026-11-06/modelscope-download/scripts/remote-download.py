"""One qualified native ModelScope resume; immutable receipts and bounded ownership."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import signal
import stat
import subprocess
import time
import urllib.request

TARGET = Path.home() / "Library/Caches/CyberAudio/RobustnessValidation/20261106-e2f34df-target1"
BASE = TARGET / "model-route-20261107"
ROOT = BASE / "full-download"
ROOT.mkdir(mode=0o700, exist_ok=False)
DATA = TARGET / "fresh-download"
PRODUCTION = Path.home() / "Library/Application Support/CyberAudio"
HELPER = BASE / "assets-artifact/CyberAudioAssets"
record = {"started_at": datetime.now(timezone.utc).isoformat(), "status": "preflight",
          "route": "explicit native ModelScope source; no --proxy or SSH forward", "maximum_cli_invocations": 1,
          "maximum_seconds": 3600, "model_loaded": False, "source_models_copied": False}
began = time.monotonic()
active = None
interrupted = False


def sha(path, length=None):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        remaining = length
        while remaining is None or remaining > 0:
            block = stream.read(1024 * 1024 if remaining is None else min(1024 * 1024, remaining))
            if not block:
                break
            value.update(block)
            if remaining is not None:
                remaining -= len(block)
    assert remaining is None or remaining == 0
    return value.hexdigest()


def snapshot():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        state = json.load(response)
    return {"instance": state["instance"], "status": state["status"], "busy": state["busy"],
            "config_sha256": sha(PRODUCTION / "config/service.json"),
            "token_sha256": sha(PRODUCTION / "config/api-token")}


def save():
    record["elapsed_seconds"] = round(time.monotonic() - began, 3)
    (ROOT / "result.private.json").write_text(json.dumps(record, indent=2) + "\n")


def stop_owned():
    if active is not None and active.poll() is None:
        active.send_signal(signal.SIGTERM)
        try:
            active.wait(timeout=15)
        except subprocess.TimeoutExpired:
            active.kill()
            active.wait(timeout=15)
            raise AssertionError("Owned helper required SIGKILL")


def on_signal(*_):
    global interrupted
    interrupted = True


signal.signal(signal.SIGTERM, on_signal)
signal.signal(signal.SIGINT, on_signal)
try:
    sustained = json.loads((BASE / "sustained/result.private.json").read_text())
    assert sustained["status"] == "qualified" and sustained["full_resume_qualified"]
    admission = json.loads((BASE / "admission-result.private.json").read_text())
    artifact = json.loads((BASE / "assets-artifact/manifest.json").read_text())
    record["helper_sha256"] = sha(HELPER)
    assert record["helper_sha256"] == artifact["helper_sha256"]
    checked = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(HELPER)],
                             capture_output=True, text=True, timeout=20)
    assert checked.returncode == 0
    record["helper_signature_valid"] = True
    lock = json.loads((TARGET / "Models.lock.json").read_text())
    assert sha(TARGET / "Models.lock.json") == artifact["model_lock_sha256"]
    assert sha(BASE / "assets-artifact/CyberAudioNative_CyberAudioCore.bundle/Models.lock.json") == artifact["model_lock_sha256"]
    configuration = json.loads((DATA / "config/service.json").read_text())
    record["production_before"] = snapshot()
    assert record["production_before"] == sustained["production_after"]
    record["config_sha256_before"] = sha(DATA / "config/service.json")
    record["token_sha256_before"] = sha(DATA / "config/api-token")
    processes = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True)
    assert not any(str(TARGET) in line and ("CyberAudioAssets download" in line or "CyberAudioService --root" in line)
                   for line in processes.splitlines())
    final_before = {}
    for entry in lock["files"]:
        kind, relative = entry["path"].split("/", 1)
        path = Path(configuration[kind + "_dir"]) / relative
        assert DATA.resolve() in path.resolve().parents
        if path.exists():
            assert stat.S_ISREG(path.lstat().st_mode)
            assert path.stat().st_size == entry["size"] and sha(path) == entry["sha256"]
            final_before[entry["path"]] = [path.stat().st_size, path.stat().st_mtime_ns, entry["sha256"]]
    assert len(final_before) == 14
    record["finals_before"] = final_before
    partials = list((DATA / "tmp/downloads").glob("*.partial"))
    assert len(partials) == 1 and partials[0].stat().st_size == 184549376
    record["partial_sha256_before"] = sha(partials[0])
    assert record["partial_sha256_before"] == admission["partial_sha256_before"]
    status = subprocess.run([str(HELPER), "status", "--root", str(DATA), "--json", "true"],
                            capture_output=True, text=True, timeout=30)
    assert status.returncode == 0
    record["status_before"] = json.loads(status.stdout)
    assert record["status_before"]["can_download"] and record["status_before"]["missing_files"] == 9
    assert record["status_before"]["resumable_bytes"] == 184549376
    assert record["status_before"]["ffmpeg_ready"]
    record["status"] = "downloading"
    save()
    last_output_size, last_output_time, last_report = 0, time.monotonic(), 0.0
    output_path = ROOT / "download.ndjson"
    with output_path.open("xb") as output:
        active = subprocess.Popen([str(HELPER), "download", "--root", str(DATA), "--source", "modelscope",
                                   "--json", "true", "--parent-pid", str(os.getpid())],
                                  stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
        record["helper_pid"] = active.pid
        while active.poll() is None:
            assert not interrupted, "Runner interrupted"
            assert time.monotonic() - began < 3600, "One-hour budget expired"
            disk = os.statvfs(DATA)
            assert disk.f_bavail * disk.f_frsize >= 3 * 1024**3, "Disk reserve below3GiB"
            size = output_path.stat().st_size
            if size != last_output_size:
                last_output_size, last_output_time = size, time.monotonic()
            assert time.monotonic() - last_output_time < 180, "No new progress for180seconds"
            if time.monotonic() - last_report >= 30:
                record["progress_log_bytes"] = size
                record["durable_partial_bytes"] = sum(p.stat().st_size for p in (DATA / "tmp/downloads").glob("*.partial"))
                save()
                print(json.dumps({"progress": {key: record[key] for key in ["status", "elapsed_seconds", "durable_partial_bytes"]}}), flush=True)
                last_report = time.monotonic()
            time.sleep(1)
        record["download_exit_code"] = active.returncode
    assert active.returncode == 0, "Native download failed; no retry"
    events = [json.loads(line) for line in output_path.read_text().splitlines()]
    assert events[-1]["event"] == "verified" and not any(row["event"] == "error" for row in events)
    record["progress_events"] = len(events)
    record["verified_files"] = []
    for entry in lock["files"]:
        kind, relative = entry["path"].split("/", 1)
        path = Path(configuration[kind + "_dir"]) / relative
        assert DATA.resolve() in path.resolve().parents and stat.S_ISREG(path.lstat().st_mode)
        assert path.stat().st_size == entry["size"] and sha(path) == entry["sha256"]
        if entry["path"] in final_before:
            assert [path.stat().st_size, path.stat().st_mtime_ns, entry["sha256"]] == final_before[entry["path"]]
        record["verified_files"].append(entry)
    tts = Path(configuration["tts_dir"]) / "model.safetensors"
    record["resumed_prefix_preserved"] = sha(tts, 184549376) == record["partial_sha256_before"]
    assert record["resumed_prefix_preserved"]
    assert not list((DATA / "tmp/downloads").glob("*.partial"))
    final_status = subprocess.run([str(HELPER), "status", "--root", str(DATA), "--json", "true"],
                                  capture_output=True, text=True, timeout=30)
    assert final_status.returncode == 0
    record["status_after"] = json.loads(final_status.stdout)
    assert record["status_after"]["models_ready"] and record["status_after"]["ffmpeg_ready"]
    record["verified_bytes"] = sum(e["size"] for e in lock["files"])
    assert len(record["verified_files"]) == 23 and record["verified_bytes"] == 5547997530
    record["status"] = "download_verified"
except Exception as exc:
    record["status"] = "failed"
    record["error_type"] = type(exc).__name__
    record["error_message"] = str(exc)[:200]
finally:
    try:
        stop_owned()
        record["owned_helper_reaped"] = active is None or active.poll() is not None
        record["production_after"] = snapshot()
        record["production_preserved"] = record["production_before"] == record["production_after"]
        record["config_token_preserved"] = (sha(DATA / "config/service.json") == record["config_sha256_before"]
                                             and sha(DATA / "config/api-token") == record["token_sha256_before"])
        assert record["production_preserved"] and record["config_token_preserved"] and record["owned_helper_reaped"]
    except Exception as exc:
        record["status"] = "preservation_verification_failed"
        record["preservation_error_type"] = type(exc).__name__
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
    print(json.dumps({"terminal": {key: record.get(key) for key in ["status", "download_exit_code", "elapsed_seconds",
          "verified_bytes", "resumed_prefix_preserved", "production_preserved", "config_token_preserved", "owned_helper_reaped", "error_type", "error_message"]}}), flush=True)
    raise SystemExit(0 if record["status"] == "download_verified" else 1)
