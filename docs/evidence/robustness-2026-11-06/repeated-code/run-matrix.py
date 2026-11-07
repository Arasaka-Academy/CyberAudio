"""Run all144 private candidates once, sequentially and without production changes."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import signal
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
plan = json.loads((ROOT / "matrix-plan.json").read_text())
build = json.loads((ROOT / "matrix-build/build.json").read_text())
pilot_build = json.loads((ROOT / "build/build.json").read_text())
config = Path.home() / "Library/Application Support/CyberAudio/config"
record = {"status": "preflight", "started_at": datetime.now(timezone.utc).isoformat()}
process = None
sources = None
began = time.monotonic()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def snapshot():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        state = json.load(response)
    return {"instance": state["instance"], "status": state["status"], "busy": state["busy"],
            "config_sha256": sha(config / "service.json"), "token_sha256": sha(config / "api-token")}


def source_snapshot():
    paths = subprocess.check_output(["rtk", "proxy", "git", "ls-files", "Sources", "Native", "scripts", "tests", "packaging", ".github", "Makefile", "Package.swift", "Package.resolved", "pyproject.toml", "uv.lock"], cwd=PROJECT, text=True).splitlines()
    return {name: sha(PROJECT / name) for name in paths}


def preserve():
    assert pilot_build["protected_inputs_sha256"] == {name: sha(name) for name in pilot_build["protected_inputs_sha256"]}
    assert build["private_objects_sha256"] == {name: sha(name) for name in build["private_objects_sha256"]}
    metadata = json.loads((ROOT.parent / "natural-reference-20261107-complete/protected-metadata-before.private.json").read_text())
    assert all([Path(name).stat().st_size, Path(name).stat().st_mtime_ns] == value for name, value in metadata.items())
    for row in plan["baselines"]:
        assert sha(row["pcm_source"]) == row["pcm_sha256"] and sha(row["tokens_source"]) == row["tokens_sha256"]
    assert plan["files_sha256"] == {name: sha(ROOT / name) for name in plan["files_sha256"]}
    return len(metadata)


def save():
    record["elapsed_seconds"] = round(time.monotonic() - began, 3)
    temporary = ROOT / "matrix-runner.json.tmp"
    temporary.write_text(json.dumps(record, indent=2) + "\n"); temporary.replace(ROOT / "matrix-runner.json")


def interrupted(*_):
    raise RuntimeError("Matrix interrupted; preserve partial outputs without automatic retry")


assert not (ROOT / "matrix-runner.json").exists()
for kind in [signal.SIGINT, signal.SIGTERM, signal.SIGALRM]:
    signal.signal(kind, interrupted)
signal.alarm(2100)
try:
    assert build["status"] == "built_no_inference" and build["shared_inputs_preserved"] and build["private_objects_preserved"]
    assert build["plan_sha256"] == sha(ROOT / "matrix-plan.json")
    record["plan_sha256"] = build["plan_sha256"]
    record["production_before"] = snapshot()
    assert record["production_before"]["status"] == "ready" and not record["production_before"]["busy"]
    record["protected_metadata_before"] = preserve()
    sources = source_snapshot()
    (ROOT / "matrix-source-before.json").write_text(json.dumps(sources, indent=2) + "\n")
    processes = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True).splitlines()
    assert not any(".context/robustness/" in line and any(word in line for word in ["CyberAudioService --root", "transcribe-reference.py", "-probe /Users/"]) for line in processes)
    executable = ROOT / "matrix-build/candidate-matrix-probe"
    assert sha(executable) == build["executable_sha256"]
    args = ["/usr/bin/caffeinate", "-i", "/usr/bin/sandbox-exec", "-f", str(Path(plan["app"]) / "Contents/Resources/offline.sb"),
            str(executable), plan["model_directory"], plan["metallib"], str(ROOT / "matrix-candidate"),
            str(ROOT / "matrix-plan.json"), str(ROOT / "candidate/report.json"), "candidate"]
    with (ROOT / "matrix-candidate.private.log").open("xb") as log:
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        record.update(status="generating", owned_pid=process.pid,
                      owned_identity=subprocess.check_output(["/bin/ps", "-p", str(process.pid), "-o", "pid=,lstart=,command="], text=True).strip())
        save()
        deadline = time.monotonic() + plan["per_stage_budget_seconds"] + 20
        while process.poll() is None:
            assert time.monotonic() < deadline and snapshot() == record["production_before"]
            time.sleep(3)
        assert process.returncode == 0, "Generation process failed; inspect original log/report"
    result = json.loads((ROOT / "matrix-candidate/report.json").read_text())
    assert result["status"] == "diagnostic_completed" and result["completed_generations"] == 144
    assert result["plan_sha256"] == sha(ROOT / "matrix-plan.json") and result["preliminary_candidate_parity"]
    record.update(status="all144_generated_not_content_accepted", candidate_report_sha256=sha(ROOT / "matrix-candidate/report.json"), completed_generations=144)
except BaseException as error:
    record.update(status="failed", error_type=type(error).__name__, error=str(error))
finally:
    signal.alarm(0)
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=12)
    try:
        record["production_after"] = snapshot()
        record["production_preserved"] = record["production_after"] == record["production_before"]
        record["source_preserved"] = sources == source_snapshot()
        record["protected_metadata_after"] = preserve()
        record["shared_and_private_inputs_preserved"] = True
        record["owned_model_exit_code"] = process.returncode if process else None
        assert record["production_preserved"] and record["source_preserved"]
    except Exception as error:
        record.update(status="failed", preservation_error=str(error))
    record["finished_at"] = datetime.now(timezone.utc).isoformat(); save()
print(json.dumps({key: value for key, value in record.items() if key not in ["production_before", "production_after", "owned_identity"]}, indent=2), flush=True)
raise SystemExit(0 if record["status"] == "all144_generated_not_content_accepted" else 1)
