"""Bounded offline native capture; preserve production and shared inputs."""
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
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
build_path = base / "native-build/build.json"
build = json.loads(build_path.read_text())
plan_path = base / "plan.json"
plan = json.loads(plan_path.read_text())
executable = base / "native-build/complete-codec-probe"
root = base / "native-capture"
receipt_path = base / "native-runner.json"
assert not root.exists() and not receipt_path.exists()
assert build["status"] == "built" and build["shared_and_production_inputs_preserved"]
assert sha(executable) == build["executable_sha256"]
assert sha(plan_path) == build["plan_sha256"]
production = Path.home() / "Library/Application Support/CyberAudio"


def snapshot():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        state = json.load(response)
    return {"instance": state["instance"], "status": state["status"], "busy": state["busy"],
            "config_sha256": sha(production / "config/service.json"),
            "token_sha256": sha(production / "config/api-token")}


receipt = {"status": "running", "started": datetime.now(timezone.utc).isoformat(),
           "production_before": snapshot(), "build_sha256": sha(build_path), "plan_sha256": sha(plan_path)}
assert receipt["production_before"]["status"] == "ready" and not receipt["production_before"]["busy"]
process = None
began = time.monotonic()


def save():
    receipt["elapsed_seconds"] = time.monotonic() - began
    temporary = receipt_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(receipt, indent=2) + "\n")
    temporary.replace(receipt_path)


def interrupted(*_):
    raise RuntimeError("Diagnostic runner interrupted")


signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)
save()
try:
    previous = json.loads((base.parent / "reference-generation-20261107/decoder-native-runner.json").read_text())
    assert previous["status"] == "completed" and previous["owned_process_exit_code"] == 0
    app = project / ".context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app"
    args = ["/usr/bin/caffeinate", "-i", "/usr/bin/sandbox-exec", "-f",
            str(app / "Contents/Resources/offline.sb"), str(executable),
            str((project / ".context/robustness/linked-models/tts").resolve()),
            str(app / "Contents/Resources/mlx.metallib"), str(root), str(plan_path)]
    with (base / "native-capture.log").open("xb") as log:
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        receipt["owned_group_pid"] = process.pid
        receipt["owned_identity"] = subprocess.check_output(
            ["/bin/ps", "-p", str(process.pid), "-o", "pid=,lstart=,command="], text=True).strip()
        save()
        process.wait(timeout=plan["swift_total_budget_seconds"] + 20)
    receipt["exit_code"] = process.returncode
    assert process.returncode == 0, "Native diagnostic failed; inspect retained log"
    result = json.loads((root / "report.json").read_text())
    assert result["status"] == "diagnostic_completed" and result["plan_sha256"] == sha(plan_path)
    assert len(result["results"]) == len(plan["cases"])
    assert all(r["original_candidate_tokens_exact"] and r["original_candidate_pcm_exact"] for r in result["results"])
    receipt["status"] = "completed"
except Exception as error:
    receipt["status"] = "failed"
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
            receipt["forced_cleanup"] = True
    receipt["owned_process_exit_code"] = process.returncode if process else None
    try:
        receipt["production_after"] = snapshot()
        receipt["production_preserved"] = receipt["production_after"] == receipt["production_before"]
        assert receipt["production_preserved"]
        assert build["preserved_input_sha256"] == {p: sha(p) for p in build["preserved_input_sha256"]}
        receipt["sdk_and_helpers_preserved"] = True
    except Exception as error:
        receipt["status"] = "failed"
        receipt["preservation_error"] = str(error)
    receipt["finished"] = datetime.now(timezone.utc).isoformat()
    save()
print(receipt["status"], round(receipt["elapsed_seconds"], 2), "seconds")
raise SystemExit(0 if receipt["status"] == "completed" else 1)
