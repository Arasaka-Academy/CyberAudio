"""Sequential native baseline check and offline complete natural reference study."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import signal
import socket
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
DATA = ROOT.parent / "post-delivery-20261107"
NATIVE = ROOT.parent / "natural-reference-20261107/native-check"
PLAN = json.loads((ROOT / "protocol.json").read_text())
APP = ROOT.parent / "stream-failure-20261107/final-bytes-stream-failure/CyberAudio.app"
BUILD = json.loads((APP.parent / "build.json").read_text())
PRODUCTION = Path.home() / "Library/Application Support/CyberAudio"
RUNTIME = ROOT.parent / "reference-parity-20261107/runtime/bin/python"
BEGAN = time.monotonic()
REPORT = ROOT / "runner.json"
process = None


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        state = json.load(response)
    return {
        "instance": state["instance"], "status": state["status"],
        "config_sha256": sha(PRODUCTION / "config/service.json"),
        "token_sha256": sha(PRODUCTION / "config/api-token"),
    }, state["busy"]


def sources():
    paths = subprocess.check_output(
        ["rtk", "proxy", "git", "ls-files", "Sources", "Native", "scripts", "tests", "packaging", ".github", "Makefile", "Package.swift", "Package.resolved", "pyproject.toml", "uv.lock"],
        cwd=PROJECT, text=True,
    ).splitlines()
    return {p: sha(PROJECT / p) for p in paths}


def protected_metadata():
    paths = {(PROJECT / p).resolve(strict=True) for p in BUILD["frozen_sha256"]}
    for model_name in ["asr", "tts"]:
        model_root = (PROJECT / ".context/robustness/linked-models" / model_name).resolve(strict=True)
        for directory, _, filenames in os.walk(model_root, followlinks=True):
            paths.update((Path(directory) / p).resolve(strict=True) for p in filenames)
    return {str(p): [p.stat().st_size, p.stat().st_mtime_ns] for p in sorted(paths)}


def interrupted(*_):
    raise RuntimeError("Natural reference study interrupted; retain partial run without automatic retry")


signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)
signal.signal(signal.SIGALRM, interrupted)
assert not REPORT.exists()
report = {
    "status": "preflight", "started_at": datetime.now(timezone.utc).isoformat(),
    "protocol_sha256": sha(ROOT / "protocol.json"),
    "scripts_sha256": {p.name: sha(p) for p in sorted(ROOT.glob("*.py"))},
    "steps": [],
}
baseline_sources = {}
baseline_metadata = {}
production_before = None


def save():
    report["elapsed_seconds"] = round(time.monotonic() - BEGAN, 3)
    temporary = REPORT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(REPORT)


def cleanup():
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)
            report["forced_kill"] = True


def run(name, arguments, environment, deadline_seconds):
    global process
    step = {"name": name, "status": "starting", "started_at": datetime.now(timezone.utc).isoformat(), "deadline_seconds": deadline_seconds}
    report["steps"].append(step)
    save()
    with (ROOT / (name + ".private.log")).open("xb") as log:
        process = subprocess.Popen(arguments, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        step.update(status="running", owned_group_pid=process.pid)
        save()
        until = time.monotonic() + deadline_seconds
        while process.poll() is None:
            assert time.monotonic() < until, name + " exceeded frozen deadline"
            state, busy = snapshot()
            assert state == production_before and not busy, "Production changed or became busy; stop only the owned study"
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                continue
    step.update(status="completed" if process.returncode == 0 else "failed", exit_code=process.returncode, finished_at=datetime.now(timezone.utc).isoformat())
    save()
    assert process.returncode == 0, name + " failed; preserve original logs/results"
    print(name, "completed", round(time.monotonic() - BEGAN, 2), "seconds", flush=True)
    process = None


signal.alarm(PLAN["execution"]["outer_timeout_seconds"])
save()
try:
    production_before, busy = snapshot()
    report["production_before"] = production_before
    assert production_before["status"] == "ready" and not busy
    with socket.socket() as s:
        assert s.connect_ex(("127.0.0.1", PLAN["native_check"]["port"])) != 0
    jobs = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True, errors="replace").splitlines()
    for row in jobs:
        if str(os.getpid()) == row.strip().split()[0]:
            continue
        if ".context/robustness/" in row:
            assert not any(name in row for name in ["CyberAudioService", "transcribe-reference.py", "Probe --"]), "Another owned model may be active"
    for relative, digest in PLAN["reference_source_sha256"].items():
        assert sha(PROJECT / relative) == digest
    assert sha(DATA / "natural-baseline/results.jsonl") == PLAN["baseline"]["raw_results_sha256"]
    assert sha(DATA / "score-natural-asr.py") == PLAN["baseline"]["qualified_scorer_sha256"]
    assert sha(APP / "Contents/Helpers/CyberAudioService") == PLAN["baseline"]["helper_sha256"]
    baseline_sources = sources()
    baseline_metadata = protected_metadata()
    (ROOT / "source-before.json").write_text(json.dumps(baseline_sources, indent=2) + "\n")
    (ROOT / "protected-metadata-before.private.json").write_text(json.dumps(baseline_metadata, indent=2) + "\n")
    report.update(status="running", source_count=len(baseline_sources), protected_metadata_count=len(baseline_metadata))
    save()
    qualification = json.loads((ROOT / "native-check-qualification.json").read_text())
    assert sha(ROOT / "native-check-qualification.json") == PLAN["native_check"]["reuse_qualification_sha256"]
    original_root = NATIVE.parent
    original_runner = json.loads((original_root / "runner.json").read_text())
    assert sha(original_root / "protocol.json") == qualification["original_protocol_sha256"]
    assert sha(original_root / "runner.json") == qualification["original_runner_sha256"]
    assert qualification["original_scripts_sha256"] == {p.name: sha(p) for p in sorted(original_root.glob("*.py"))}
    native = json.loads((NATIVE / "runner.json").read_text())
    assert sha(NATIVE / "runner.json") == qualification["native_runner_sha256"]
    assert sha(NATIVE / "results.jsonl") == qualification["native_result_sha256"]
    assert native["status"] == "whole_test_transcribed_not_quality_accepted" and native["completed_requests"] == PLAN["native_check"]["count"]
    assert native["owned_helper_exit_code"] == 0 and native["owned_listener_closed"]
    baseline = {r["id"]: r for r in map(json.loads, (DATA / "natural-baseline/results.jsonl").read_text().splitlines())}
    rows = list(map(json.loads, (NATIVE / "results.jsonl").read_text().splitlines()))
    assert [r["id"] for r in rows] == PLAN["native_check"]["ids"]
    keys = PLAN["native_check"]["compared_fields"]
    assert all(r["http_status"] == 200 and {k:r["response"][k] for k in keys} == {k:baseline[r["id"]]["response"][k] for k in keys} for r in rows)
    assert not list((NATIVE / "service/tmp").glob("request-*"))
    try:
        os.kill(native["owned_helper_pid"], 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("Prior native Helper remains alive")
    assert original_runner["production_before"] == production_before
    report.update(native_baseline_reproduced=len(rows), native_check_reused=True, native_check_qualification_sha256=sha(ROOT / "native-check-qualification.json"))
    environment = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false", HF_HOME=str(ROOT / "private-cache/huggingface"), XDG_CACHE_HOME=str(ROOT / "private-cache"), PYTHONDONTWRITEBYTECODE="1")
    run("reference", ["/usr/bin/caffeinate", "-i", "/usr/bin/sandbox-exec", "-f", str(APP / "Contents/Resources/offline.sb"), str(RUNTIME), "-u", str(ROOT / "transcribe-reference.py")], environment, PLAN["execution"]["reference_total_timeout_seconds"] + 30)
    reference = json.loads((ROOT / "reference.json").read_text())
    assert reference["status"] == "complete_reference_not_quality_accepted" and reference["completed_utterances"] == PLAN["data"]["count"]
    report["status"] = "reference_study_complete_pending_scoring"
except BaseException as error:
    report.update(status="failed", error_type=type(error).__name__, error=str(error))
finally:
    signal.alarm(0)
    cleanup()
    try:
        after, _ = snapshot()
        report["production_after"] = after
        report["production_preserved"] = after == production_before
        report["source_preserved"] = baseline_sources == sources() if baseline_sources else True
        report["protected_metadata_preserved"] = baseline_metadata == protected_metadata() if baseline_metadata else True
        report["scripts_preserved"] = report["scripts_sha256"] == {p.name: sha(p) for p in sorted(ROOT.glob("*.py"))}
        with socket.socket() as s:
            report["native_listener_closed"] = s.connect_ex(("127.0.0.1", PLAN["native_check"]["port"])) != 0
        assert all(report[k] for k in ["production_preserved", "source_preserved", "protected_metadata_preserved", "scripts_preserved", "native_listener_closed"])
    except Exception as error:
        report.update(status="failed", preservation_error_type=type(error).__name__, preservation_error=str(error))
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
print(json.dumps({k: report[k] for k in ["status", "elapsed_seconds"]}), flush=True)
raise SystemExit(0 if report["status"] == "reference_study_complete_pending_scoring" else 1)
