from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import time
import urllib.request

base = Path(__file__).resolve().parent
project = base.parents[2]
production = Path.home()/"Library/Application Support/CyberAudio"
root = base/"m1-seeded-tts-probe"
assert not root.exists()
executable = base/"seeded-tts-build/seeded-tts-probe"
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
build = json.loads((base/"seeded-tts-build/build.json").read_text())
assert sha(executable) == build["executable_sha256"]
def snapshot():
    with urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=5) as response:
        state = json.load(response)
    return {"instance": state["instance"],
            "config_sha256": sha(production/"config/service.json"),
            "token_sha256": sha(production/"config/api-token")}

receipt = {"status": "running", "started": datetime.now(timezone.utc).isoformat(),
           "production_before": snapshot(), "build": build,
           "purpose": "Fixed-seed baseline reproduction; no inference parameter change"}
path = base/"seeded-tts-result.json"
def save():
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(receipt, indent=2)+"\n")
    temporary.replace(path)
save()
began = time.monotonic()
try:
    args = ["/usr/bin/caffeinate", "-i", "/usr/bin/sandbox-exec", "-f",
            str(project/"dist/CyberAudio.app/Contents/Resources/offline.sb"),
            str(executable), str((project/".context/robustness/linked-models/tts").resolve()),
            str(project/"dist/CyberAudio.app/Contents/Resources/mlx.metallib"), str(root)]
    result = subprocess.run(args, timeout=1900)
    receipt["exit_code"] = result.returncode
    assert result.returncode == 0
    report = json.loads((root/"report.json").read_text())
    assert report["status"] == "diagnostic_completed"
    receipt["observation"] = report["observation"]
    receipt["production_after"] = snapshot()
    assert receipt["production_after"] == receipt["production_before"]
    receipt["production_preserved"] = True
    receipt["status"] = "completed"
except Exception as error:
    receipt["status"] = "failed"
    receipt["error"] = str(error)
    raise
finally:
    receipt["elapsed_seconds"] = time.monotonic()-began
    receipt["finished"] = datetime.now(timezone.utc).isoformat()
    save()
