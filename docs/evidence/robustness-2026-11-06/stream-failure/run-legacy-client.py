"""Bounded App-client compatibility check against the retained actual-model failure."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import plistlib
import signal
import shutil
import socket
import subprocess
import time
import urllib.request

base = Path(__file__).resolve().parent
project = base.parents[2]
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
original = base.parent / "m4-acceptance-20261106/seeded-service-build/CyberAudioSeededService"
assert sha(original) == "13379551ed4db5568c1b824794a372e8593b3afc1fec4b625c64a23da7127a13"
current = base / "final-bytes-stream-failure/CyberAudio.app"
build = json.loads((current.parent / "build.json").read_text())
assert build["status"] == "built"
for name, row in build["files"].items():
    assert sha(current / name) == row["sha256"]
processes = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True).splitlines()
assert not [row for row in processes if "CyberAudioService" in row and "/.context/robustness/" in row]
with socket.socket() as probe:
    assert probe.connect_ex(("127.0.0.1", 18896)) != 0
data = Path.home() / "Library/Application Support/CyberAudio"


def production():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        state = json.load(response)
    return {"instance": state["instance"], "status": state["status"], "busy": state["busy"],
            "config_sha256": sha(data / "config/service.json"), "token_sha256": sha(data / "config/api-token")}


root = base / "legacy-model-data"
app = base / "LegacyFailureProbe.app"
assert not root.exists() and not app.exists()
for relative in ["Contents/MacOS", "Contents/Helpers", "Contents/Resources"]:
    (app / relative).mkdir(parents=True, exist_ok=True)
shutil.copy2(original, app / "Contents/Helpers/CyberAudioService")
for name in ["offline.sb", "mlx.metallib"]:
    shutil.copy2(current / "Contents/Resources" / name, app / "Contents/Resources" / name)
(app / "Contents/Info.plist").write_bytes(plistlib.dumps({
    "CFBundleExecutable": "LegacyFailureProbe", "CFBundleIdentifier": "local.cyberaudio.validation.streamfailurelegacyclient",
    "CFBundlePackageType": "APPL", "LSUIElement": True,
}))
result = {"status": "running", "started": datetime.now(timezone.utc).isoformat(),
          "scope": "Current production App client code with retained private seed-196 original-model Helper; no production/default changes",
          "original_helper_sha256": sha(original), "client_source_sha256": sha(base / "LegacyFailureProbe.swift"),
          "production_before": production(), "port": 18896}
assert result["production_before"]["status"] == "ready" and not result["production_before"]["busy"]
receipt = base / "legacy-client.json"
assert not receipt.exists()
started = time.monotonic()


def save():
    result["elapsed_seconds"] = time.monotonic() - started
    receipt.write_text(json.dumps(result, indent=2) + "\n")


save()
process = None
try:
    subprocess.run(["swiftc", "-swift-version", "5", "-parse-as-library", "-target", "arm64-apple-macos26.0",
                    "Sources/CyberAudio/AudioWorkspace.swift", "Sources/CyberAudio/BackendController.swift",
                    str(base / "LegacyFailureProbe.swift"), "-o", str(app / "Contents/MacOS/LegacyFailureProbe")],
                   cwd=project, check=True, capture_output=True, text=True, timeout=60)
    for path in [app / "Contents/Helpers/CyberAudioService", app]:
        subprocess.run(["codesign", "--force", "--sign", "-", str(path)], check=True, capture_output=True, timeout=30)
    subprocess.run([str(current / "Contents/Helpers/CyberAudioAssets"), "prepare", "--root", str(root), "--port", "18896"],
                   check=True, capture_output=True, timeout=30)
    config = root / "config/service.json"
    value = json.loads(config.read_text())
    value.update(asr_dir=str((project / ".context/robustness/linked-models/asr").resolve()),
                 tts_dir=str((project / ".context/robustness/linked-models/tts").resolve()), ffmpeg="/opt/homebrew/bin/ffmpeg")
    config.write_text(json.dumps(value) + "\n")
    result["isolated_config_sha256"] = sha(config)
    result["isolated_token_sha256"] = sha(root / "config/api-token")
    result["bundled_helper_sha256"] = sha(app / "Contents/Helpers/CyberAudioService")
    result["client_binary_sha256"] = sha(app / "Contents/MacOS/LegacyFailureProbe")
    save()
    with (base / "legacy-client.log").open("xb") as output:
        process = subprocess.Popen(["/usr/bin/caffeinate", "-i", str(app / "Contents/MacOS/LegacyFailureProbe"),
                                    "--data-dir", str(root)], stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        result["owned_group_pid"] = process.pid
        save()
        process.wait(timeout=360)
    result["exit_code"] = process.returncode
    assert process.returncode == 0
    client = json.loads((root / "client-result.json").read_text())
    assert client["status"] == "passed" and client["failure_code"] == "generation_limit"
    result["client"] = client
    lines = (root / "logs/service.log").read_text()
    assert lines.count("service_started backend=mlx-swift") == 1
    assert lines.count("request_done id=") == 2 and "status=failed" in lines and "status=completed" in lines
    assert not list((root / "tmp").glob("request-*"))
    result["status"] = "passed"
except Exception as error:
    result.update(status="failed", error_type=type(error).__name__, error=str(error))
    if isinstance(error, subprocess.CalledProcessError) and error.stderr:
        (base / "legacy-build.stderr.private").write_bytes(error.stderr.encode() if isinstance(error.stderr, str) else error.stderr)
finally:
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)
    result["production_after"] = production()
    result["production_preserved"] = result["production_before"] == result["production_after"]
    result["original_helper_preserved"] = sha(original) == result["original_helper_sha256"]
    processes = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True).splitlines()
    result["owned_processes_absent"] = not [row for row in processes if str(app) in row]
    with socket.socket() as probe:
        result["listener_closed"] = probe.connect_ex(("127.0.0.1", 18896)) != 0
    if not all(result[key] for key in ["production_preserved", "original_helper_preserved", "owned_processes_absent", "listener_closed"]):
        result["status"] = "failed"
    result["finished"] = datetime.now(timezone.utc).isoformat()
    save()
print(json.dumps({key: value for key, value in result.items() if key not in ["production_before", "production_after"]}, indent=2))
raise SystemExit(0 if result["status"] == "passed" else 1)
