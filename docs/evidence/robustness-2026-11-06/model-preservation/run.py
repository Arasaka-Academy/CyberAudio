"""Exercise only disposable directory deletion; original weights stay outside it."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

base = Path(__file__).resolve().parent
project = base.parents[2]
stage = sys.argv[1]
assert stage in {"baseline", "fixed"}
receipt_path = base / (stage + "-result.json")
assert not receipt_path.exists()
source = (project / ".context/robustness/linked-models/tts").resolve(strict=True)
assert not source.is_relative_to(base)
root = base / (stage + "-model")
assert not root.exists() and root.parent == base
root.mkdir()
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
source_files = [p for p in source.rglob("*") if p.is_file()]
protected = {str(p): sha(p) for p in source_files}
for original in source.iterdir():
    if original.is_file():
        (root / original.name).symlink_to(original)
assert list(root.glob("*.safetensors"))
(root / "preserve-user-data.txt").write_text("User data must survive a failed model load")
(root / "speech_tokenizer").write_text("Unexpected user file")
assert all(p.is_symlink() for p in root.iterdir() if p.name not in {"preserve-user-data.txt", "speech_tokenizer"})
production = Path.home() / "Library/Application Support/CyberAudio"

def snapshot():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        state = json.load(response)
    return {"instance": state["instance"], "status": state["status"],
            "config_sha256": sha(production / "config/service.json"),
            "token_sha256": sha(production / "config/api-token")}

frozen_files = [*project.glob("dist/*.dmg"), project / "dist/CyberAudio.app/Contents/MacOS/CyberAudio",
                project / "dist/CyberAudio.app/Contents/Helpers/CyberAudioService",
                project / "dist/CyberAudio.app/Contents/Helpers/CyberAudioAssets"]
frozen = {str(p): sha(p) for p in frozen_files}
build = json.loads((base / (stage + "-build/build.json")).read_text())
executable = base / (stage + "-build/model-preservation-probe")
assert sha(executable) == build["executable_sha256"]
receipt = {"status": "running", "stage": stage, "started": datetime.now(timezone.utc).isoformat(),
           "production_before": snapshot(), "source_files_before": protected, "frozen_before": frozen,
           "build_sha256": sha(base / (stage + "-build/build.json"))}
assert receipt["production_before"]["status"] == "ready"
began = time.monotonic()
process = None

def save():
    receipt["elapsed_seconds"] = time.monotonic() - began
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

try:
    args = ["/usr/bin/sandbox-exec", "-f", str(project / "dist/CyberAudio.app/Contents/Resources/offline.sb"),
            str(executable), str(root), str(project / "dist/CyberAudio.app/Contents/Resources/mlx.metallib"),
            str(base / (stage + "-model-report.json"))]
    with (base / (stage + ".log")).open("xb") as log:
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        receipt["owned_group_pid"] = process.pid
        save()
        process.wait(timeout=180)
    receipt["exit_code"] = process.returncode
    report = json.loads((base / (stage + "-model-report.json")).read_text())
    receipt["report"] = report
    expected = 2 if stage == "baseline" else 0
    assert process.returncode == expected
    assert report["rejected"] and report["recognized_tokenizer_error"]
    assert report["sentinel_and_user_file_preserved"] == (stage == "fixed")
    receipt["status"] = "reproduced" if stage == "baseline" else "passed"
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
    receipt["production_after"] = snapshot()
    receipt["original_models_preserved"] = protected == {str(p): sha(p) for p in source_files}
    receipt["production_preserved"] = receipt["production_after"] == receipt["production_before"]
    receipt["frozen_preserved"] = frozen == {str(p): sha(p) for p in frozen_files}
    assert receipt["original_models_preserved"] and receipt["production_preserved"] and receipt["frozen_preserved"]
    receipt["finished"] = datetime.now(timezone.utc).isoformat()
    save()
print(json.dumps({key: receipt[key] for key in ["stage", "status", "exit_code", "original_models_preserved", "production_preserved", "frozen_preserved"]}))
raise SystemExit(0 if receipt["status"] in {"reproduced", "passed"} else 1)
