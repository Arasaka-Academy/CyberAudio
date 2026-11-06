"""Run one bounded owned build/test; no model or production process management."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import signal
import subprocess
import sys
import time

base = Path(__file__).resolve().parent
project = base.parents[2]
name, budget, *command = sys.argv[1:]
assert command and name.replace("-", "").isalnum()
log, receipt = base / (name + ".log"), base / (name + ".json")
assert not log.exists() and not receipt.exists()
environment = dict(os.environ)
environment["CYBERAUDIO_NATIVE_SCRATCH"] = str(base / "native-build")
environment["CYBERAUDIO_BUILD_CACHE"] = str(project / ".context/local-cache")
environment["CYBERAUDIO_BUILD_JOBS"] = "6"
result = {"status": "running", "name": name, "command": command,
          "started": datetime.now(timezone.utc).isoformat(),
          "budget_seconds": int(budget), "native_scratch": environment["CYBERAUDIO_NATIVE_SCRATCH"],
          "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
start = time.monotonic()


def save():
    result["elapsed_seconds"] = time.monotonic() - start
    target = receipt.with_suffix(".tmp")
    target.write_text(json.dumps(result, indent=2) + "\n")
    target.replace(receipt)


save()
process = None
try:
    with log.open("xb") as handle:
        process = subprocess.Popen(command, cwd=project, env=environment, stdout=handle,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        result["owned_group_pid"] = process.pid
        save()
        process.wait(timeout=int(budget))
        result["exit_code"] = process.returncode
        result["status"] = "passed" if process.returncode == 0 else "failed"
except subprocess.TimeoutExpired:
    result["status"] = "timed_out"
finally:
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
    result["finished"] = datetime.now(timezone.utc).isoformat()
    result["log_sha256"] = hashlib.sha256(log.read_bytes()).hexdigest()
    save()
print(json.dumps(result, indent=2))
print("\n".join(log.read_text(errors="replace").splitlines()[-14:]))
raise SystemExit(0 if result["status"] == "passed" else 1)
