"""Run the frozen pilot offline while preserving production and frozen inputs."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import signal
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
PLAN = json.loads((ROOT / "protocol.json").read_text())
APP = ROOT.parent / "stream-failure-20261107/final-bytes-stream-failure/CyberAudio.app"
RUNTIME = ROOT.parent / "reference-parity-20261107/runtime/bin/python"
PRODUCTION = Path.home() / "Library/Application Support/CyberAudio"
REPORT = ROOT / "runner.json"
BEGAN = time.monotonic()
process = None


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def production():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        state = json.load(response)
    assert state["status"] == "ready" and not state["busy"], "Production busy: stop owned pilot only"
    return {"instance": state["instance"], "status": state["status"],
            "config_sha256": sha(PRODUCTION / "config/service.json"), "token_sha256": sha(PRODUCTION / "config/api-token")}


def current_metadata(before):
    return {p: [Path(p).stat().st_size, Path(p).stat().st_mtime_ns] for p in before}


def interrupted(*_):
    raise RuntimeError("Context pilot interrupted; preserve partial results without retry")


for sig in [signal.SIGTERM, signal.SIGINT, signal.SIGALRM]:
    signal.signal(sig, interrupted)
assert not REPORT.exists()
before = json.loads((ROOT / "production-before.private.json").read_text())
sources = json.loads((ROOT / "source-before.json").read_text())
metadata = json.loads((ROOT / "protected-metadata-before.private.json").read_text())
scripts = {p.name: sha(p) for p in sorted(ROOT.glob("*.py"))}
report = {"status": "preflight", "started_at": datetime.now(timezone.utc).isoformat(),
          "protocol_sha256": sha(ROOT / "protocol.json"), "scripts_sha256": scripts,
          "source_count": len(sources), "protected_file_count": len(metadata)}


def save():
    report["elapsed_seconds"] = round(time.monotonic() - BEGAN, 3)
    temporary = REPORT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(REPORT)


save()
signal.alarm(PLAN["execution"]["outer_timeout_seconds"])
try:
    assert production() == before
    assert {p: sha(PROJECT / p) for p in sources} == sources
    assert current_metadata(metadata) == metadata
    for row in subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True).splitlines():
        if row.strip().split()[0] == str(os.getpid()):
            continue
        if ".context/robustness/" in row:
            assert not any(p in row for p in ["CyberAudioService", "transcribe-reference.py", "transcribe-context.py", "Probe --"]), "Another owned model process may be active"
    environment = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false",
                       HF_HOME=str(ROOT / "private-cache/huggingface"), XDG_CACHE_HOME=str(ROOT / "private-cache"), PYTHONDONTWRITEBYTECODE="1")
    arguments = ["/usr/bin/caffeinate", "-i", "/usr/bin/sandbox-exec", "-f", str(APP / "Contents/Resources/offline.sb"),
                 str(RUNTIME), "-u", str(ROOT / "transcribe-context.py")]
    with (ROOT / "inference.private.log").open("xb") as log:
        process = subprocess.Popen(arguments, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        report.update(status="running", owned_group_pid=process.pid)
        save()
        while process.poll() is None:
            assert production() == before
            assert time.monotonic() - BEGAN < PLAN["execution"]["total_timeout_seconds"] + 30
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                continue
    report["owned_exit_code"] = process.returncode
    assert process.returncode == 0
    inference = json.loads((ROOT / "inference.json").read_text())
    assert inference["status"] == "pilot_complete_pending_scoring"
    assert inference["completed_generations"] == PLAN["execution"]["total_generations"]
    report["status"] = "pilot_complete_pending_scoring"
except BaseException as error:
    report.update(status="failed", error_type=type(error).__name__, error=str(error))
finally:
    signal.alarm(0)
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)
            report["forced_kill"] = True
    try:
        report.update(production_preserved=production() == before,
                      source_preserved={p: sha(PROJECT / p) for p in sources} == sources,
                      protected_metadata_preserved=current_metadata(metadata) == metadata,
                      scripts_preserved=scripts == {p.name: sha(p) for p in sorted(ROOT.glob("*.py"))})
        assert all(report[k] for k in ["production_preserved", "source_preserved", "protected_metadata_preserved", "scripts_preserved"])
        if process is not None:
            groups = [int(row.split()[0]) for row in subprocess.check_output(["/bin/ps", "-axo", "pgid=,pid="], text=True).splitlines() if row.strip()]
            report["owned_group_absent"] = process.pid not in groups
            assert report["owned_group_absent"]
    except Exception as error:
        report.update(status="failed", preservation_error_type=type(error).__name__, preservation_error=str(error))
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
print(json.dumps({k: report[k] for k in ["status", "elapsed_seconds"]}), flush=True)
raise SystemExit(0 if report["status"] == "pilot_complete_pending_scoring" else 1)
