"""Current signed App and original complete driver on publicly downloaded files."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import signal
import socket
import subprocess
import time
import urllib.request

TARGET = Path.home() / "Library/Caches/CyberAudio/RobustnessValidation/20261106-e2f34df-target1"
BASE = TARGET / "model-route-20261107"
ROOT = BASE / "fresh-acceptance"
ROOT.mkdir(mode=0o700, exist_ok=False)
APP = BASE / "current-source/CyberAudio.app"
DRIVER = BASE / "current-source/verify-native"
DATA = ROOT / "data"
DOWNLOAD = TARGET / "fresh-download"
CORPUS = Path.home() / "Library/Caches/CyberAudio/CoreMLValidation/20261105/corpus"
PRODUCTION = Path.home() / "Library/Application Support/CyberAudio"
PORT = 18906
protocol = json.loads((BASE / "fresh-acceptance-protocol.json").read_text())
manifest = json.loads((BASE / "current-source/manifest.json").read_text())
record = {"started_at": datetime.now(timezone.utc).isoformat(), "status": "preflight", "port": PORT,
          "maximum_seconds": 1800, "asr_count": 100, "strict_soak_started": False, "models_copied": False,
          "scope": "Current App, Service, Assets and unmodified complete native driver; new root and public downloaded models"}
began = time.monotonic()
process = None
interrupted = False


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def snapshot():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        state = json.load(response)
    return {"instance": state["instance"], "status": state["status"], "busy": state["busy"],
            "config_sha256": sha(PRODUCTION / "config/service.json"),
            "token_sha256": sha(PRODUCTION / "config/api-token")}


def app_preserved(app, files):
    return all((app / name).is_symlink() and os.readlink(app / name) == value["target"] if value["type"] == "symlink"
               else sha(app / name) == value["sha256"] for name, value in files.items())


def save():
    record["elapsed_seconds"] = round(time.monotonic() - began, 3)
    temporary = ROOT / "result.private.tmp"
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.replace(ROOT / "result.private.json")


def on_signal(*_):
    global interrupted
    interrupted = True


signal.signal(signal.SIGTERM, on_signal)
signal.signal(signal.SIGINT, on_signal)
try:
    downloaded = json.loads((BASE / "full-download/result.private.json").read_text())
    assert downloaded["status"] == "download_verified" and downloaded["download_exit_code"] == 0
    record["production_before"] = snapshot()
    assert record["production_before"] == downloaded["production_after"]
    record["config_sha256_before"] = sha(DOWNLOAD / "config/service.json")
    record["token_sha256_before"] = sha(DOWNLOAD / "config/api-token")
    assert sha(DRIVER) == protocol["driver_sha256"] == manifest["driver_sha256"]
    assert app_preserved(APP, manifest["final_app_files"])
    assert app_preserved(TARGET / "artifacts/CyberAudio.app", manifest["base_app_files"])
    with socket.socket() as stream:
        assert stream.connect_ex(("127.0.0.1", PORT)) != 0
    processes = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True)
    assert not any(str(TARGET) in line and ("CyberAudioService --root" in line or "CyberAudioAssets download" in line)
                   for line in processes.splitlines())
    configuration = json.loads((DOWNLOAD / "config/service.json").read_text())
    locks = json.loads((TARGET / "Models.lock.json").read_text())["files"]
    for item in locks:
        kind, relative = item["path"].split("/", 1)
        path = Path(configuration[kind + "_dir"]) / relative
        assert DOWNLOAD.resolve() in path.resolve().parents and not path.is_symlink()
        assert path.stat().st_size == item["size"] and sha(path) == item["sha256"]
    record["verified_download_files"] = len(locks)
    record["model_metadata_before"] = {str(path): [path.stat().st_size, path.stat().st_mtime_ns]
                                       for kind in ["asr", "tts"] for path in Path(configuration[kind + "_dir"]).rglob("*") if path.is_file()}
    rows = json.loads((CORPUS / "corpus.json").read_text())
    selected = [{key: row[key] for key in ["id", "language", "audio_sha256", "reference"]} for row in rows]
    corpus_digest = hashlib.sha256(json.dumps(selected, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert len(rows) == 100 and corpus_digest == protocol["corpus_semantic_sha256"]
    assert all(sha(CORPUS / row["audio"]) == row["audio_sha256"] for row in rows)
    record["corpus_semantic_sha256"] = corpus_digest
    links = ROOT / "linked-fresh-models"
    links.mkdir()
    for kind in ["asr", "tts"]:
        (links / kind).symlink_to(configuration[kind + "_dir"], target_is_directory=True)
    record["driver_sha256"] = sha(DRIVER)
    record["status"] = "running"
    save()
    with (ROOT / "native-full.private.log").open("xb") as output:
        process = subprocess.Popen(["/usr/bin/caffeinate", "-i", str(DRIVER), "--app", str(APP), "--root", str(DATA),
                                    "--models", str(links), "--corpus", str(CORPUS), "--port", str(PORT), "--asr-count", "100"],
                                   stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        record["owned_group_pid"] = process.pid
        last_report = 0.0
        while process.poll() is None:
            assert not interrupted, "Owned runner interrupted"
            assert time.monotonic() - began < 1800, "Fresh acceptance exceeded30minutes"
            disk = os.statvfs(ROOT)
            assert disk.f_bavail * disk.f_frsize >= 3 * 1024**3, "Disk reserve below3GiB"
            if time.monotonic() - last_report >= 30:
                save()
                last_report = time.monotonic()
            time.sleep(1)
        record["exit_code"] = process.returncode
    native = json.loads((DATA / "reports/native-service.json").read_text())
    assert record["exit_code"] == 0 and native["status"] == "passed"
    assert len(native["asr_corpus"]) == 100 and len(native["tts_voices"]) == 9
    assert native["helper_sha256"] == manifest["final_app_files"]["Contents/Helpers/CyberAudioService"]["sha256"]
    record["asr_corpus_samples"] = len(native["asr_corpus"])
    record["tts_voices"] = len(native["tts_voices"])
    record["status"] = "passed"
except Exception as exc:
    record["status"] = "failed"
    record["error_type"] = type(exc).__name__
    record["error_message"] = str(exc)[:200]
finally:
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)
        record["forced_cleanup"] = True
    try:
        record["production_after"] = snapshot()
        record["production_preserved"] = record["production_before"] == record["production_after"]
        record["download_config_token_preserved"] = (sha(DOWNLOAD / "config/service.json") == record["config_sha256_before"]
                                                      and sha(DOWNLOAD / "config/api-token") == record["token_sha256_before"])
        metadata = {str(path): [path.stat().st_size, path.stat().st_mtime_ns]
                    for kind in ["asr", "tts"] for path in Path(configuration[kind + "_dir"]).rglob("*") if path.is_file()}
        record["download_model_metadata_preserved"] = metadata == record["model_metadata_before"]
        record["current_app_preserved"] = app_preserved(APP, manifest["final_app_files"])
        record["frozen_app_preserved"] = app_preserved(TARGET / "artifacts/CyberAudio.app", manifest["base_app_files"])
        with socket.socket() as stream:
            record["owned_port_closed"] = stream.connect_ex(("127.0.0.1", PORT)) != 0
        record["temporary_request_directories"] = len(list((DATA / "tmp").glob("request-*")))
        assert all(record[key] for key in ["production_preserved", "download_config_token_preserved", "download_model_metadata_preserved",
                                           "current_app_preserved", "frozen_app_preserved", "owned_port_closed"])
        assert record["temporary_request_directories"] == 0
    except Exception as exc:
        record["status"] = "preservation_verification_failed"
        record["preservation_error_type"] = type(exc).__name__
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
print(json.dumps({key: value for key, value in record.items() if key not in ["production_before", "production_after", "model_metadata_before"]}, indent=2))
raise SystemExit(0 if record["status"] == "passed" else 1)
