"""One bounded strict repeat, after the first runner stopped; no release actions."""
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

root = Path(__file__).resolve().parent
data = root / "data-soak-r2"
assert not data.exists()
owner = json.loads((root/".acceptance-owner").read_text())
assert owner["source_commit"] == "e2f34df30eab49e526a3dfd2fa7d6fffbfb81d0b"
assert json.loads((root/"soak-result.json").read_text())["status"] == "failed"
assert not subprocess.run(["/bin/ps", "-p", "53772", "-o", "lstart="], capture_output=True, text=True).stdout.strip()
for port in (18867, 18876):
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", port)) != 0
production = Path.home()/"Library/Application Support/CyberAudio"
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def snapshot():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        ready = json.load(response)
    return {"instance": ready["instance"], "status": ready["status"],
        "config_sha256": digest(production/"config/service.json"),
        "token_sha256": digest(production/"config/api-token")}
before = snapshot()
assert before["status"] == "ready"
began = time.monotonic()
receipt = {"status": "running", "started": datetime.now(timezone.utc).isoformat(),
    "runtime_fix_commit": owner["source_commit"], "production_before": before,
    "scope": "Same strict 7200-second workload; preserve terminal failure request/context. No acceptance threshold changed.",
    "driver_sha256": digest(root/"artifacts/verify-native-reporting"), "port": 18876}
def save():
    receipt["elapsed_seconds"] = time.monotonic()-began
    path = root/"soak-r2-result.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(receipt, indent=2)+"\n")
    temporary.replace(path)
save()
process = None
def on_signal(number, frame):
    raise RuntimeError("Runner cancelled")
for number in (signal.SIGTERM, signal.SIGINT):
    signal.signal(number, on_signal)
try:
    app = root/"artifacts/CyberAudio.app"
    assert digest(app/"Contents/Helpers/CyberAudioService") == "cc556b65e263e16218db4315ed8585dccaede7b5f539be7bbd68a4b04e841a89"
    arguments = ["/usr/bin/caffeinate", "-i", str(root/"artifacts/verify-native-reporting"),
        "--app", str(app), "--root", str(data), "--models", str(root/"models"),
        "--corpus", str(Path.home()/"Library/Caches/CyberAudio/CoreMLValidation/20261105/corpus"),
        "--port", "18876", "--soak-seconds", "7200"]
    with (root/"soak-r2.log").open("xb") as log:
        process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        receipt["child_pid"] = process.pid
        save()
        process.wait(timeout=9600)
    receipt["exit_code"] = process.returncode
    native = json.loads((data/"reports/native-service.json").read_text())
    soak = json.loads((data/"reports/soak.json").read_text())
    receipt["native_report_status"] = native.get("status")
    receipt["soak_report_status"] = soak.get("status")
    receipt["production_after"] = snapshot()
    assert receipt["production_after"] == before
    receipt["production_preserved"] = True
    with socket.socket() as probe:
        probe.settimeout(1)
        receipt["owned_listener_closed"] = probe.connect_ex(("127.0.0.1", 18876)) != 0
    assert receipt["owned_listener_closed"]
    receipt["temporary_request_count"] = len(list((data/"tmp").glob("request-*")))
    assert receipt["temporary_request_count"] == 0
    receipt["status"] = "passed" if process.returncode == 0 and native.get("status") == "passed" and soak.get("status") == "passed" and soak["duration_seconds"] >= 7200 else "failed"
except Exception as error:
    receipt["status"] = "cancelled" if str(error) == "Runner cancelled" else "failed"
    receipt["error_type"] = type(error).__name__
    receipt["error"] = str(error)
finally:
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)
    receipt["finished"] = datetime.now(timezone.utc).isoformat()
    save()
