"""Prepare an isolated, hash-locked reference runtime using the available mirror."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import signal
import subprocess
import time

base = Path(__file__).resolve().parent
receipt_path = base / "runtime-setup.json"
assert not receipt_path.exists()
environment = dict(os.environ, UV_HTTP_TIMEOUT="20", UV_HTTP_RETRIES="1")
python = base / "runtime/bin/python"
commands = [
    ["uv", "pip", "compile", "--python", str(python), "--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple",
     "--generate-hashes", "--only-binary", ":all:", "requirements.in", "--output-file", "requirements.lock"],
    ["uv", "pip", "sync", "--python", str(python), "--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple",
     "--require-hashes", "--only-binary", ":all:", "requirements.lock"],
]
start = time.monotonic()
receipt = {"status": "running", "started": datetime.now(timezone.utc).isoformat(), "steps": [],
           "scope": "New private Python environment only; no production app/runtime/config/models modified"}

def save():
    receipt["elapsed_seconds"] = time.monotonic() - start
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

save()
try:
    for index, command in enumerate(commands):
        process = None
        began = time.monotonic()
        step = {"command": command, "status": "running"}
        receipt["steps"].append(step)
        save()
        try:
            with (base / f"runtime-setup-{index}.log").open("xb") as log:
                process = subprocess.Popen(command, cwd=base, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                step["owned_group_pid"] = process.pid
                save()
                step["exit_code"] = process.wait(timeout=300)
            step["status"] = "passed" if process.returncode == 0 else "failed"
            assert process.returncode == 0, f"Reference setup step {index} failed"
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=15)
            step["elapsed_seconds"] = time.monotonic() - began
            save()
    receipt["status"] = "passed"
    receipt["lock_sha256"] = hashlib.sha256((base / "requirements.lock").read_bytes()).hexdigest()
except Exception as error:
    receipt["status"] = "failed"
    receipt["error_type"] = type(error).__name__
    receipt["error"] = str(error)
finally:
    receipt["finished"] = datetime.now(timezone.utc).isoformat()
    save()
print(json.dumps({key: receipt.get(key) for key in ["status", "elapsed_seconds", "lock_sha256", "error_type"]}))
raise SystemExit(0 if receipt["status"] == "passed" else 1)
