"""On-target acceptance: normal app termination, crash cleanup, restart, offline boundary."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import urllib.request

root = Path.home() / "Library/Application Support/CyberAudio"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
report = {"started": time.time(), "steps": []}
parser = argparse.ArgumentParser()
parser.add_argument("--driver", default="/Applications/CuaDriver.app/Contents/MacOS/cua-driver")
driver = parser.parse_args().driver


def state():
    try:
        with opener.open("http://127.0.0.1:8765/ready", timeout=1) as response:
            return json.load(response)
    except Exception:
        return None


def app_pid():
    found = subprocess.check_output(["/usr/bin/pgrep", "-f", "^/Applications/CyberAudio.app/Contents/MacOS/CyberAudio$"], text=True)
    pids = [int(p) for p in found.split()]
    assert len(pids) == 1
    return pids[0]


def launch():
    response = subprocess.run([driver, "launch_app", '{"bundle_id":"local.cyberaudio.mac"}'], capture_output=True, text=True, timeout=25)
    assert response.returncode == 0, "Background app launch failed"
    begin = time.monotonic()
    for _ in range(300):
        value = state()
        if value and value["status"] == "ready":
            return {"ready_after_ms": round((time.monotonic() - begin) * 1000, 1), "ready": value, "pid": app_pid()}
        time.sleep(0.2)
    raise RuntimeError("App failed to become ready")


for kind, sig in [("normal_sigterm", signal.SIGTERM), ("forced_app_crash", signal.SIGKILL)]:
    pid = app_pid()
    before = state()
    assert before and before["status"] == "ready"
    begin = time.monotonic()
    os.kill(pid, sig)
    stopped = False
    for _ in range(150):
        try:
            with socket.create_connection(("127.0.0.1", 8765), timeout=0.2):
                pass
        except OSError:
            stopped = True
            break
        time.sleep(0.1)
    assert stopped, "Owned backend listener survived app exit"
    elapsed = round((time.monotonic() - begin) * 1000, 1)
    time.sleep(3)
    assert state() is None, "Backend unexpectedly resurrected"
    record = {"kind": kind, "listener_stopped_ms": elapsed, "no_resurrection_after_s": 3, "restart": launch()}
    assert record["restart"]["ready"]["instance"] != before["instance"]
    report["steps"].append(record)
    print(json.dumps({"kind": kind, "stopped_ms": elapsed, "restart_ms": record["restart"]["ready_after_ms"]}), flush=True)

probe = subprocess.run(["/usr/bin/sandbox-exec", "-f", str(root / "config/offline.sb"),
    str(root / "runtime/.venv/bin/python"), "-c", "import socket; socket.create_connection(('1.1.1.1',443),2)"], capture_output=True, text=True, timeout=5)
report["outbound_denied"] = probe.returncode != 0 and "PermissionError" in probe.stderr
assert report["outbound_denied"]
report["daemon_absent"] = subprocess.run(["/bin/launchctl", "print", "system/local.qwen3-asr"], capture_output=True).returncode != 0
assert report["daemon_absent"]
report["finished"] = time.time()
(root / "reports/lifecycle.json").write_text(json.dumps(report, indent=2))
print("LIFECYCLE_COMPLETE", flush=True)
