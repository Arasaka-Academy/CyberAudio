"""Run reference comparison with a bounded process group and offline environment."""
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
project = base.parents[3]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
build = json.loads((base.parent / "native-build/build.json").read_text())
plan = json.loads((base / "plan.json").read_text())
production = Path.home() / "Library/Application Support/CyberAudio"
path = base / "reference-runner.json"
assert not path.exists()


def snapshot():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        state = json.load(response)
    return {"instance": state["instance"], "status": state["status"], "busy": state["busy"],
            "config_sha256": sha(production / "config/service.json"),
            "token_sha256": sha(production / "config/api-token")}


receipt = {"status": "running", "started": datetime.now(timezone.utc).isoformat(),
           "production_before": snapshot(), "source_sha256": sha(base / "compare-reference.py"),
           "requirements_sha256": sha(base.parents[1] / "reference-parity-20261107/requirements.lock"), "plan_sha256": sha(base / "plan.json")}
began = time.monotonic()
process = None


def save():
    receipt["elapsed_seconds"] = time.monotonic() - began
    path.write_text(json.dumps(receipt, indent=2) + "\n")


def interrupted(*_):
    raise RuntimeError("Reference runner interrupted")


signal.signal(signal.SIGINT, interrupted)
signal.signal(signal.SIGTERM, interrupted)
save()
try:
    native = json.loads((base.parent / "reference-runner.json").read_text())
    assert native["status"] == "completed" and native["owned_process_exit_code"] == 0
    app = project / ".context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app"
    environment = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false",
                       HF_HOME=str(base / "private-cache/huggingface"), XDG_CACHE_HOME=str(base / "private-cache"),
                       PYTHONDONTWRITEBYTECODE="1")
    args = ["/usr/bin/caffeinate", "-i", "/usr/bin/sandbox-exec", "-f", str(app / "Contents/Resources/offline.sb"),
            str(base.parents[1] / "reference-parity-20261107/runtime/bin/python"), str(base / "compare-reference.py")]
    with (base / "reference.log").open("xb") as log:
        process = subprocess.Popen(args, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        receipt["owned_group_pid"] = process.pid
        receipt["owned_identity"] = subprocess.check_output(
            ["/bin/ps", "-p", str(process.pid), "-o", "pid=,lstart=,command="], text=True).strip()
        save()
        process.wait(timeout=plan["reference_total_budget_seconds"] + 20)
    receipt["exit_code"] = process.returncode
    assert process.returncode == 0, "Reference failed; inspect retained log"
    result = json.loads((base / "reference-comparison.json").read_text())
    assert result["status"] == "diagnostic_completed" and len(result["results"]) == len(plan["cases"])
    receipt["status"] = "completed"
except Exception as error:
    receipt.update(status="failed", error_type=type(error).__name__, error=str(error))
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
        receipt.update(status="failed", preservation_error=str(error))
    receipt["finished"] = datetime.now(timezone.utc).isoformat()
    save()
print(receipt["status"], round(receipt["elapsed_seconds"], 2), "seconds")
raise SystemExit(0 if receipt["status"] == "completed" else 1)
