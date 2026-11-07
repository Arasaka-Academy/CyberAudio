"""Run the unchanged complete native driver against current privately built code."""
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

BASE = Path(__file__).resolve().parent
PROJECT = BASE.parents[2]
ROOT = BASE / "current-source"
APP = ROOT / "CyberAudio.app"
DATA = ROOT / "data-local"
MODELS = PROJECT / ".context/robustness/linked-models"
CORPUS = PROJECT / ".context/robustness/m4-acceptance-20261106/corpus"
PORT = 18905
build = json.loads((ROOT / "build.private.json").read_text())
assert not DATA.exists() and not (ROOT / "local-result.private.json").exists()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def production():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        state = json.load(response)
    root = Path.home() / "Library/Application Support/CyberAudio"
    return {"instance": state["instance"], "status": state["status"],
            "config_sha256": sha(root / "config/service.json"), "token_sha256": sha(root / "config/api-token")}


def model_metadata():
    return {str(path): [path.stat().st_size, path.stat().st_mtime_ns]
            for kind in ["asr", "tts"] for path in (MODELS / kind).resolve().rglob("*") if path.is_file()}


def app_preserved():
    return all((APP / name).is_symlink() and os.readlink(APP / name) == value["target"] if value["type"] == "symlink"
               else sha(APP / name) == value["sha256"] for name, value in build["app_files"].items())


assert app_preserved()
assert all(sha(PROJECT / name) == digest for name, digest in build["source_sha256"].items())
with socket.socket() as stream:
    assert stream.connect_ex(("127.0.0.1", PORT)) != 0
rows = json.loads((CORPUS / "corpus.json").read_text())
assert len(rows) == 100 and all(sha(CORPUS / row["audio"]) == row["audio_sha256"] for row in rows)
record = {"started_at": datetime.now(timezone.utc).isoformat(), "status": "running", "port": PORT,
          "maximum_seconds": 1800, "driver_sha256": sha(ROOT / "verify-native"), "production_before": production(),
          "model_metadata_before": model_metadata(), "scope": "Full original100-corpus native driver; no strict soak or physical acceptance"}
assert record["production_before"]["status"] == "ready"
began = time.monotonic()
process = None


def save():
    record["elapsed_seconds"] = round(time.monotonic() - began, 3)
    temporary = ROOT / "local-result.private.tmp"
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.replace(ROOT / "local-result.private.json")


try:
    save()
    with (ROOT / "local-full.private.log").open("xb") as output:
        process = subprocess.Popen(["/usr/bin/caffeinate", "-i", str(ROOT / "verify-native"), "--app", str(APP),
                                    "--root", str(DATA), "--models", str(MODELS), "--corpus", str(CORPUS),
                                    "--port", str(PORT), "--asr-count", "100"],
                                   stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        record["owned_group_pid"] = process.pid
        save()
        record["exit_code"] = process.wait(timeout=1800)
    native = json.loads((DATA / "reports/native-service.json").read_text())
    assert record["exit_code"] == 0 and native["status"] == "passed"
    assert len(native["asr_corpus"]) == 100 and len(native["tts_voices"]) == 9
    assert native["helper_sha256"] == build["app_files"]["Contents/Helpers/CyberAudioService"]["sha256"]
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
    record["production_after"] = production()
    record["production_preserved"] = record["production_before"] == record["production_after"]
    record["model_metadata_preserved"] = record["model_metadata_before"] == model_metadata()
    record["frozen_preserved"] = all(sha(PROJECT / name) == digest for name, digest in build["frozen_sha256"].items())
    record["app_preserved"] = app_preserved()
    record["temporary_request_directories"] = len(list((DATA / "tmp").glob("request-*")))
    with socket.socket() as stream:
        record["owned_port_closed"] = stream.connect_ex(("127.0.0.1", PORT)) != 0
    if not all(record[key] for key in ["production_preserved", "model_metadata_preserved", "frozen_preserved", "app_preserved", "owned_port_closed"]):
        record["status"] = "preservation_verification_failed"
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
print(json.dumps({key: value for key, value in record.items() if key not in ["production_before", "production_after", "model_metadata_before"]}, indent=2))
raise SystemExit(0 if record["status"] == "passed" else 1)
