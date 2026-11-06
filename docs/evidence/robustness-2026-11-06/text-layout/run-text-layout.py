"""Run only the fixed paired diagnostic; retain terminal state and owned process identity."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import signal
import subprocess
import time
import urllib.request

base = Path(__file__).resolve().parent
project = base.parents[2]
root = base / "m1-text-layout-probe"
assert not root.exists()
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
build = json.loads((base / "text-layout-build/build.json").read_text())
assert build["status"] == "built" and build["shared_and_production_inputs_preserved"]
executable = base / "text-layout-build/text-layout-probe"
plan = base / "text-layout-plan.json"
assert sha(executable) == build["executable_sha256"] and sha(plan) == build["plan_sha256"]
production = Path.home() / "Library/Application Support/CyberAudio"


def snapshot():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        state = json.load(response)
    return {"instance": state["instance"], "status": state["status"], "busy": state["busy"],
            "config_sha256": sha(production / "config/service.json"),
            "token_sha256": sha(production / "config/api-token")}


receipt = {"status": "running", "started": datetime.now(timezone.utc).isoformat(),
           "scope": "Ten fixed paired generations with current dialect routing and original sampling",
           "production_before": snapshot(), "build_sha256": sha(base / "text-layout-build/build.json")}
assert receipt["production_before"]["status"] == "ready" and not receipt["production_before"]["busy"]
path = base / "text-layout-result.json"
assert not path.exists()
process = None
began = time.monotonic()


def save():
    receipt["elapsed_seconds"] = time.monotonic() - began
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(receipt, indent=2) + "\n")
    temporary.replace(path)


def interrupted(*_):
    raise RuntimeError("Diagnostic runner cancelled")


signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)
save()
try:
    app = base / "final-bytes-language-routing/CyberAudio.app"
    args = ["/usr/bin/caffeinate", "-i", "/usr/bin/sandbox-exec", "-f",
            str(app / "Contents/Resources/offline.sb"), str(executable),
            str((project / ".context/robustness/linked-models/tts").resolve()),
            str(app / "Contents/Resources/mlx.metallib"), str(root), str(plan),
            str(base / "m1-seeded-tts-probe/report.json")]
    with (base / "text-layout.log").open("xb") as log:
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        receipt["owned_group_pid"] = process.pid
        receipt["owned_identity"] = subprocess.check_output(["/bin/ps", "-p", str(process.pid), "-o", "pid=,lstart=,command="], text=True).strip()
        save()
        process.wait(timeout=420)
    receipt["exit_code"] = process.returncode
    assert process.returncode == 0
    result = json.loads((root / "report.json").read_text())
    assert result["status"] == "diagnostic_completed" and result["completed_generations"] == 10
    assert result["plan_sha256"] == build["plan_sha256"]
    assert result["results"][0]["original_failure_parity"]
    receipt["status"] = "completed"
except Exception as error:
    receipt["status"] = "failed"; receipt["error_type"] = type(error).__name__; receipt["error"] = str(error)
finally:
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=15); receipt["forced_cleanup"] = True
    receipt["owned_process_exit_code"] = process.returncode if process else None
    try:
        receipt["production_after"] = snapshot()
        receipt["production_preserved"] = receipt["production_after"] == receipt["production_before"]
        assert receipt["production_preserved"]
        assert build["preserved_input_sha256"] == {name: sha(Path(name)) for name in build["preserved_input_sha256"]}
        receipt["sdk_and_helpers_preserved"] = True
    except Exception as error:
        receipt["status"] = "failed"; receipt["preservation_error"] = str(error)
    receipt["finished"] = datetime.now(timezone.utc).isoformat(); save()
raise SystemExit(0 if receipt["status"] == "completed" else 1)
