"""One bounded actual public download using the per-range retry candidate.

Owns validation assets only. Never installs an App or starts model inference.
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
assert json.loads((root / ".acceptance-owner").read_text())["source_commit"] == "e2f34df30eab49e526a3dfd2fa7d6fffbfb81d0b"
prior = json.loads((root / "download-r2-result.json").read_text())
assert prior["status"] == "failed" and prior["finished"]
artifact = root / "artifacts/download-retry-candidate"
manifest = json.loads((artifact / "manifest.json").read_text())
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
for name, digest in manifest["files"].items():
    assert sha(artifact / name) == digest
helper = artifact / "CyberAudioAssets"
subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(helper)], check=True)
assert sha(artifact / "CyberAudioNative_CyberAudioCore.bundle/Models.lock.json") == sha(root / "Models.lock.json")
data = root / "fresh-download"
production = Path.home() / "Library/Application Support/CyberAudio"


def snapshot():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        state = json.load(response)
    return {"instance": state["instance"], "status": state["status"],
            "config_sha256": sha(production / "config/service.json"),
            "token_sha256": sha(production / "config/api-token")}


configuration = {name: sha(data / "config" / name) for name in ["service.json", "api-token"]}
report = {"status": "running", "started": datetime.now(timezone.utc).isoformat(),
          "maximum_seconds": 3600, "disk_reserve_bytes": 3 * 1024 ** 3,
          "scope": "One actual Assets CLI run with bounded internal range retries; retained public partials; no model inference or App install",
          "candidate": manifest, "runner_sha256": sha(Path(__file__)),
          "production_before": snapshot(), "model_inference_not_run": True}
assert report["production_before"]["status"] == "ready"
assert report["production_before"] == prior["production_after"]
path = root / "download-retry-result.json"
assert not path.exists()
began = time.monotonic()
process = None
idle = None
cancelled = False


def save():
    report["elapsed_seconds"] = time.monotonic() - began
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(path)


def stop(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=15)
            report["forced_cleanup"] = True


def cli(command):
    result = subprocess.run([str(helper), command, "--root", str(data), "--json", "true"],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, "Assets status/verification failed"
    return json.loads(result.stdout)


def interrupt(*_):
    global cancelled
    cancelled = True


signal.signal(signal.SIGTERM, interrupt)
signal.signal(signal.SIGINT, interrupt)
save()
try:
    report["before"] = cli("status")
    assert report["before"]["can_download"] and not report["before"]["invalid_files"] and not report["before"]["invalid_partials"]
    idle = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
    with (root / "download-retry.ndjson").open("xb") as output, (root / "download-retry.stderr.log").open("xb") as errors:
        process = subprocess.Popen([str(helper), "download", "--root", str(data), "--proxy", "http://127.0.0.1:7890",
                                    "--parent-pid", str(os.getpid()), "--json", "true"], stdout=output, stderr=errors)
        report["child_pid"] = process.pid
        save()
        while process.poll() is None:
            assert not cancelled, "Runner cancelled"
            assert time.monotonic() - began < 3600, "Actual download time budget reached"
            stat = os.statvfs(data)
            assert stat.f_bavail * stat.f_frsize > 3 * 1024 ** 3, "Disk reserve below 3 GiB"
            time.sleep(1)
        report["exit_code"] = process.returncode
    report["final_status"] = cli("status")
    assert process.returncode == 0 and report["final_status"]["models_ready"], "Actual download did not complete"
    report["verification"] = cli("verify")
    config = json.loads((data / "config/service.json").read_text())
    files = json.loads((root / "Models.lock.json").read_text())["files"]
    for row in files:
        component, name = row["path"].split("/", 1)
        asset = Path(config[component + "_dir"]) / name
        assert data.resolve() in asset.resolve().parents and not asset.is_symlink()
        assert asset.stat().st_size == row["size"] and sha(asset) == row["sha256"]
    assert len(files) == 23 and sum(row["size"] for row in files) == 5547997530
    report["independently_verified_files"] = files
    report["independently_verified_bytes"] = 5547997530
    report["status"] = "passed"
except Exception as error:
    report["status"] = "cancelled" if cancelled else "failed"
    report["error"] = str(error)
finally:
    if process is not None:
        stop(process)
    if idle is not None:
        stop(idle)
    try:
        report["production_after"] = snapshot()
        report["production_preserved"] = report["production_after"] == report["production_before"]
        report["configuration_token_preserved"] = configuration == {name: sha(data / "config" / name) for name in configuration}
        assert report["production_preserved"] and report["configuration_token_preserved"]
    except Exception as error:
        report["status"] = "failed"
        report["preservation_error"] = str(error)
    report["finished"] = datetime.now(timezone.utc).isoformat()
    save()
raise SystemExit(0 if report["status"] == "passed" else 1)
