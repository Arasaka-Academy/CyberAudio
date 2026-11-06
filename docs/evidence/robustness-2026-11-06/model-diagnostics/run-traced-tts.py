from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import signal
import subprocess
import time
import urllib.request

base = Path(__file__).resolve().parent
project = base.parents[2]
assert json.loads((base/"m1-asr-followup/reports/asr-followup.json").read_text())["owned_helper_exit_code"] == 0
root = base/"m1-traced-tts-probe"
assert not root.exists()
build = json.loads((base/"traced-tts-build/executable-build.json").read_text())
executable = base/"traced-tts-build/traced-tts-probe"
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert sha(executable) == build["executable_sha256"]
production = Path.home()/"Library/Application Support/CyberAudio"
def snapshot():
    with urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=5) as response:
        health = json.load(response)
    return {"instance": health["instance"], "config_sha256": sha(production/"config/service.json"),
            "token_sha256": sha(production/"config/api-token")}
receipt = {"status": "running", "started": datetime.now(timezone.utc).isoformat(),
           "production_before": snapshot(), "build": build,
           "scope": "Private tracing and temperature hypotheses; production model parameters unchanged"}
path = base/"traced-tts-result.json"
def save():
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(receipt, indent=2)+"\n")
    temporary.replace(path)
save()
process = None
began = time.monotonic()
try:
    args = ["/usr/bin/caffeinate", "-i", "/usr/bin/sandbox-exec", "-f",
        str(project/"dist/CyberAudio.app/Contents/Resources/offline.sb"), str(executable),
        str((project/".context/robustness/linked-models/tts").resolve()),
        str(project/"dist/CyberAudio.app/Contents/Resources/mlx.metallib"), str(root),
        str(base/"m1-seeded-tts-probe/report.json")]
    process = subprocess.Popen(args, start_new_session=True)
    receipt["owned_group_pid"] = process.pid
    save()
    process.wait(timeout=1000)
    receipt["exit_code"] = process.returncode
    assert process.returncode == 0
    report = json.loads((root/"report.json").read_text())
    assert report["status"] == "diagnostic_completed"
    receipt["production_after"] = snapshot()
    assert receipt["production_after"] == receipt["production_before"]
    receipt["production_preserved"] = True
    receipt["status"] = "completed"
except Exception as error:
    receipt["status"] = "failed"; receipt["error"] = str(error)
    raise
finally:
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=15)
            receipt["forced_cleanup"] = True
    receipt["elapsed_seconds"] = time.monotonic()-began
    receipt["finished"] = datetime.now(timezone.utc).isoformat()
    save()
