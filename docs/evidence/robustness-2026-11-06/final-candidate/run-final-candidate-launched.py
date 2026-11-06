"""Bounded full fixed-corpus acceptance of isolated current binaries."""
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

base = Path(__file__).resolve().parent
project = base.parents[2]
candidate = base / "final-bytes-939050f"
build = json.loads((candidate / "build.json").read_text())
assert build["status"] == "built" and build["frozen_preserved"]
app = candidate / "CyberAudio.app"
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
for relative, value in build["files"].items():
    assert sha(app / relative) == value["sha256"]
for relative, digest in build["frozen_sha256"].items():
    assert sha(project / relative) == digest
for filename in ["tts-matrix-r2-result.json"]:
    prior = json.loads((base / filename).read_text())
    assert prior["status"] == "completed" and prior["finished"] and prior["exit_code"] == 0
roundtrip = json.loads((base / "m1-tts-matrix-roundtrip/reports/asr-followup.json").read_text())
assert roundtrip["status"] == "diagnostic_completed" and roundtrip["owned_helper_exit_code"] == 0
rows = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,lstart=,command="], capture_output=True, text=True, check=True).stdout.splitlines()
assert not [r for r in rows if any(w in r for w in ["/tts-matrix-probe", "/m1-tts-matrix-roundtrip", "/m1-traced-tts-probe", "/m1-seeded-tts-probe"]) and "python3 -" not in r]
root = candidate / "data-full"
assert not root.exists()
corpus = base / "corpus"
manifest = json.loads((corpus / "corpus.json").read_text())
assert len(manifest) == 100
for row in manifest:
    assert sha(corpus / row["audio"]) == row["audio_sha256"]
port = 18882
with socket.socket() as probe:
    assert probe.connect_ex(("127.0.0.1", port)) != 0

def production():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        state = json.load(response)
    data = Path.home() / "Library/Application Support/CyberAudio"
    return {"instance": state["instance"], "status": state["status"],
            "config_sha256": sha(data / "config/service.json"), "token_sha256": sha(data / "config/api-token")}

report = {"status": "running", "started": datetime.now(timezone.utc).isoformat(), "port": port,
          "scope": "Current isolated App/Service/Assets; full fixed 100 corpus, formats/voices/stream/cancel/update/EOF and actual App TERM/KILL/restart; no long soak or physical acceptance",
          "source_commit": build["source_commit"], "build_sha256": sha(candidate / "build.json"),
          "driver_sha256": sha(candidate / "verify-native"), "corpus_manifest_sha256": sha(corpus / "corpus.json"),
          "production_before": production(), "maximum_seconds": 1800}
assert report["production_before"]["status"] == "ready"
path = candidate / "full-result.json"
assert not path.exists()
began = time.monotonic()
process = None

def save():
    report["elapsed_seconds"] = time.monotonic() - began
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(path)

save()
try:
    with (candidate / "full.log").open("xb") as output:
        process = subprocess.Popen(["/usr/bin/caffeinate", "-i", str(candidate / "verify-native"),
            "--app", str(app), "--root", str(root), "--models", str(project / ".context/robustness/linked-models"),
            "--corpus", str(corpus), "--port", str(port), "--asr-count", "100"],
            stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        report["owned_group_pid"] = process.pid
        report["owned_process_identity"] = subprocess.run(["/bin/ps", "-p", str(process.pid), "-o", "pid=,lstart=,command="], capture_output=True, text=True, check=True).stdout.strip()
        save()
        print("Started isolated current-byte full acceptance; owned PID", process.pid, flush=True)
        report["exit_code"] = process.wait(timeout=1800)
    native = json.loads((root / "reports/native-service.json").read_text())
    report["native_report_status"] = native["status"]
    assert report["exit_code"] == 0 and native["status"] == "passed"
    assert native["helper_sha256"] == build["files"]["Contents/Helpers/CyberAudioService"]["sha256"]
    assert len(native["asr_corpus"]) == 100 and len(native["tts"]) == 9
    assert not list((root / "tmp").glob("request-*"))
    report["temporary_request_cleanup"] = True
    report["status"] = "passed"
except Exception as error:
    report["status"] = "failed"
    report["error_type"] = type(error).__name__
    report["error"] = str(error)
finally:
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)
        report["forced_cleanup"] = True
    report["production_after"] = production()
    report["production_preserved"] = report["production_after"] == report["production_before"]
    report["frozen_preserved"] = all(sha(project / relative) == digest for relative, digest in build["frozen_sha256"].items())
    report["candidate_bytes_preserved"] = all(sha(app / relative) == value["sha256"] for relative, value in build["files"].items())
    with socket.socket() as probe:
        report["owned_listener_closed"] = probe.connect_ex(("127.0.0.1", port)) != 0
    if not all(report[key] for key in ["production_preserved", "frozen_preserved", "candidate_bytes_preserved", "owned_listener_closed"]):
        report["status"] = "failed"
    report["finished"] = datetime.now(timezone.utc).isoformat()
    save()
print(json.dumps({key: report[key] for key in ["status", "elapsed_seconds", "finished", "production_preserved", "frozen_preserved", "owned_listener_closed"]}, indent=2))
raise SystemExit(0 if report["status"] == "passed" else 1)
