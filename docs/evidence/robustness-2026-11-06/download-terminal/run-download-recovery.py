"""One bounded recovery run after terminal failure and a successful Range probe.

This owns only the existing validation download root. It never starts inference.
"""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import signal
import subprocess
import time
import urllib.request

root = Path(__file__).resolve().parent
assert json.loads((root/".acceptance-owner").read_text())["source_commit"] == "e2f34df30eab49e526a3dfd2fa7d6fffbfb81d0b"
prior = json.loads((root/"target-completion.json").read_text())
assert prior["status"] == "failed" and prior["finished"]
probe = json.loads((root/"download-range-probe.json").read_text())
assert probe["exit_code"] == 0 and probe["received_bytes"] == probe["expected_bytes"] == 8388608
data = root/"fresh-download"
helper = root/"artifacts/CyberAudio.app/Contents/Helpers/CyberAudioAssets"
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert sha(helper) == "719cf86bd9bb4e9cfffb32e29af40d6a8dc4f5cad007273b8856b02f1f8b5d46"
production = Path.home()/"Library/Application Support/CyberAudio"
def snapshot():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        state = json.load(response)
    return {"instance": state["instance"], "status": state["status"],
            "config_sha256": sha(production/"config/service.json"),
            "token_sha256": sha(production/"config/api-token")}
report = {"status": "running", "started": datetime.now(timezone.utc).isoformat(),
          "maximum_attempts": 2, "per_attempt_seconds": 3600, "total_budget_seconds": 7300,
          "scope": "Resume the actual Assets CLI on retained public partials; no model process or App installation",
          "production_before": snapshot(), "attempts": []}
configuration = {name: sha(data/"config"/name) for name in ["service.json", "api-token"]}
started = time.monotonic()
active = None
idle = None
cancelled = False
path = root/"download-r2-result.json"
assert not path.exists()
def save():
    report["elapsed_seconds"] = time.monotonic()-started
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2)+"\n"); tmp.replace(path)
def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=15)
            report["forced_cleanup"] = True
def bounds():
    assert not cancelled, "Runner cancelled"
    assert time.monotonic()-started < 7300, "Recovery time budget reached"
    fs = os.statvfs(data)
    assert fs.f_bavail*fs.f_frsize > 3*1024**3, "Disk reserve below 3 GiB"
def cli(command):
    result = subprocess.run([str(helper), command, "--root", str(data), "--json", "true"],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0
    return json.loads(result.stdout)
def interrupt(*_):
    global cancelled
    cancelled = True
signal.signal(signal.SIGTERM, interrupt)
signal.signal(signal.SIGINT, interrupt)
save()
try:
    idle = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
    for number in [1, 2]:
        bounds()
        state = cli("status")
        if state["models_ready"]:
            break
        assert state["can_download"] and not state["invalid_files"] and not state["invalid_partials"]
        attempt = {"number": number, "started": datetime.now(timezone.utc).isoformat(), "before": state}
        report["attempts"].append(attempt); save()
        attempt_start = time.monotonic()
        environment = os.environ.copy()
        environment["CFNETWORK_DIAGNOSTICS"] = "1"
        with (root/f"download-r2-{number}.ndjson").open("xb") as output, (root/f"download-r2-{number}.network.log").open("xb") as errors:
            active = subprocess.Popen([str(helper), "download", "--root", str(data), "--proxy",
                "http://127.0.0.1:7890", "--json", "true"], stdout=output, stderr=errors, env=environment)
            attempt["pid"] = active.pid; save()
            while active.poll() is None:
                bounds()
                if time.monotonic()-attempt_start >= 3600:
                    attempt["budget_expired"] = True; stop(active); break
                time.sleep(1)
            attempt["exit_code"] = active.returncode
            attempt["elapsed_seconds"] = time.monotonic()-attempt_start
            active = None
        attempt["after"] = cli("status"); save()
        if attempt["after"]["models_ready"]:
            break
        if number == 1:
            for _ in range(5):
                bounds(); time.sleep(1)
    state = cli("status")
    report["final_status"] = state
    assert state["models_ready"], "Public assets incomplete after bounded recovery"
    verified = cli("verify")
    config = json.loads((data/"config/service.json").read_text())
    files = json.loads((root/"Models.lock.json").read_text())["files"]
    for row in files:
        component, name = row["path"].split("/", 1)
        asset = Path(config[component+"_dir"])/name
        assert data.resolve() in asset.resolve().parents and not asset.is_symlink()
        assert asset.stat().st_size == row["size"] and sha(asset) == row["sha256"]
    assert len(files) == 23 and sum(x["size"] for x in files) == 5547997530
    report["public_download"] = {"status": "passed", "files": files, "verified_bytes": 5547997530,
                                  "verification": verified, "model_inference_not_run": True}
    report["status"] = "passed"
except Exception as error:
    report["status"] = "cancelled" if cancelled else "failed"
    report["error"] = str(error)
finally:
    if active is not None:
        stop(active)
    if idle is not None:
        stop(idle)
    try:
        report["production_after"] = snapshot()
        report["production_preserved"] = report["production_after"] == report["production_before"]
        report["configuration_token_preserved"] = configuration == {name: sha(data/"config"/name) for name in configuration}
        assert report["production_preserved"] and report["configuration_token_preserved"]
    except Exception as error:
        report["status"] = "failed"; report["preservation_error"] = str(error)
    report["finished"] = datetime.now(timezone.utc).isoformat(); save()
