"""Run the two fixed TTS arms serially, requiring exact baseline admission."""
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
PLAN = json.loads((ROOT / "protocol.json").read_text())
PRODUCTION = Path.home() / "Library/Application Support/CyberAudio/config"
began = time.monotonic()
process = None
record = {"status": "preflight", "started_at": datetime.now(timezone.utc).isoformat(), "completed_arms": [], "asr_started": False}


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def production():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        state = json.load(response)
    return {"instance": state["instance"], "status": state["status"], "busy": state["busy"],
            "config_sha256": sha(PRODUCTION / "service.json"), "token_sha256": sha(PRODUCTION / "api-token")}


def source_snapshot():
    paths = subprocess.check_output(["rtk", "proxy", "git", "ls-files", "Sources", "Native", "scripts", "tests", "packaging", ".github", "Makefile", "Package.swift", "Package.resolved", "pyproject.toml", "uv.lock"], cwd=PROJECT, text=True).splitlines()
    return {relative: sha(PROJECT / relative) for relative in paths}


def metadata():
    expected = json.loads((ROOT.parent / "natural-reference-20261107-complete/protected-metadata-before.private.json").read_text())
    assert all([Path(path).stat().st_size, Path(path).stat().st_mtime_ns] == value for path, value in expected.items())
    return len(expected)


def save():
    record["elapsed_seconds"] = round(time.monotonic() - began, 3)
    path = ROOT / "runner.json"
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(record, indent=2) + "\n"); temp.replace(path)


def interrupt(*_):
    raise RuntimeError("Fixed repeated-code study interrupted; do not automatically retry")


signal.signal(signal.SIGTERM, interrupt)
signal.signal(signal.SIGINT, interrupt)
signal.signal(signal.SIGALRM, interrupt)
signal.alarm(1500)
try:
    assert not (ROOT / "runner.json").exists()
    record["production_before"] = production()
    assert record["production_before"]["status"] == "ready" and not record["production_before"]["busy"]
    record["protected_metadata_count"] = metadata()
    sources = source_snapshot()
    (ROOT / "source-before.json").write_text(json.dumps(sources, indent=2) + "\n")
    record["protocol_sha256"] = sha(ROOT / "protocol.json")
    assert PLAN["files_sha256"] == {name: sha(ROOT / name) for name in PLAN["files_sha256"]}
    build = json.loads((ROOT / "build/build.json").read_text())
    assert build["status"] == "built_no_model" and build["shared_inputs_preserved"]
    frozen_build = json.loads((Path(PLAN["app"]).parent / "build.json").read_text())
    frozen = {relative: sha(PROJECT / relative) for relative in frozen_build["frozen_sha256"]}
    assert frozen == frozen_build["frozen_sha256"]
    reference_path = Path(PLAN["baseline_reference"])
    assert sha(reference_path) == PLAN["baseline_reference_sha256"]
    references = json.loads(reference_path.read_text())["results"]
    processes = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True).splitlines()
    assert not any(".context/robustness/" in line and any(word in line for word in ["CyberAudioService --root", "transcribe-reference.py", "-probe /Users/"]) for line in processes)
    save()
    all_results = {}
    for arm in ["baseline", "candidate"]:
        assert production() == record["production_before"]
        executable = ROOT / "build" / (arm + "-probe")
        assert sha(executable) == build["executables_sha256"][arm]
        record["status"] = "running_" + arm; save()
        args = ["/usr/bin/sandbox-exec", "-f", str(Path(PLAN["app"]) / "Contents/Resources/offline.sb"), str(executable),
                PLAN["model_directory"], PLAN["metallib"], str(ROOT / arm), str(ROOT / "protocol.json"), PLAN["legacy_baseline"]]
        with (ROOT / (arm + ".private.log")).open("xb") as log:
            process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            record["owned_model_pid"] = process.pid; save()
            deadline = time.monotonic() + 650
            while process.poll() is None:
                assert time.monotonic() < deadline, "Original bounded diagnostic deadline reached"
                assert production() == record["production_before"], "Production changed or became busy"
                time.sleep(3)
            assert process.returncode == 0, "Private TTS arm failed; preserve its output"
        result = json.loads((ROOT / arm / "report.json").read_text())
        assert result["status"] == "diagnostic_completed" and len(result["results"]) == 6
        assert [row["case"] for row in result["results"]] == PLAN["cases"]
        assert all(sha(ROOT / arm / row["pcm_file"]) == row["pcm_sha256"] for row in result["results"])
        all_results[arm] = result["results"]
        if arm == "baseline":
            assert all(left["tokens"] == right["tokens"] and left["pcm_sha256"] == right["pcm_sha256"] and left["case"] == right["case"] for left, right in zip(result["results"], references)), "Fresh original baseline must exactly reproduce all six retained paths before the candidate"
            record["baseline_exact_matches"] = 6
        record["completed_arms"].append(arm)
        save()
        print(json.dumps({"arm": arm, "generations": 6, "eos_results": [{"id": row["id"], "eos": row["eos_seen"], "tokens": row["token_count"], "seconds": row["audio_seconds"]} for row in result["results"]]}), flush=True)
    original, candidate = all_results["baseline"], all_results["candidate"]
    controls_exact = all(left["tokens"] == right["tokens"] and left["pcm_sha256"] == right["pcm_sha256"] for left, right in zip(original, candidate) if not left["id"].startswith("known-failure"))
    failures = [row for row in candidate if row["id"].startswith("known-failure")]
    actual_eos = all(row["eos_seen"] for row in failures)
    repeats_exact = failures[0]["tokens"] == failures[1]["tokens"] and failures[0]["pcm_sha256"] == failures[1]["pcm_sha256"]
    prefix_exact = True
    for left, right in zip(original, candidate):
        if not left["id"].startswith("known-failure"):
            continue
        prefix_exact &= left["tokens"][:47] == right["tokens"][:47]
        prefix_exact &= (ROOT / "baseline" / left["pcm_file"]).read_bytes()[:168960] == (ROOT / "candidate" / right["pcm_file"]).read_bytes()[:168960]
    record.update(status="generations_complete_not_content_accepted", normal_controls_exact=controls_exact,
                  actual_eos_in_both_failure_repeats=actual_eos, candidate_repeats_exact=repeats_exact,
                  first47_tokens_first44_frames_exact=prefix_exact,
                  necessary_generation_gate_passed=bool(controls_exact and actual_eos and repeats_exact and prefix_exact))
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
        record["production_after"] = production()
        record["production_preserved"] = record["production_after"] == record["production_before"]
        record["source_preserved"] = sources == source_snapshot()
        record["frozen_preserved"] = frozen == {relative: sha(PROJECT / relative) for relative in frozen}
        record["protected_metadata_after_count"] = metadata()
        record["owned_model_exit_code"] = process.returncode if process is not None else None
        assert record["production_preserved"] and record["source_preserved"] and record["frozen_preserved"]
    except Exception as error:
        record.update(status="failed", preservation_error_type=type(error).__name__, preservation_error=str(error))
    record["finished_at"] = datetime.now(timezone.utc).isoformat(); save()
print(json.dumps({key: value for key, value in record.items() if key not in ["production_before", "production_after"]}, indent=2), flush=True)
raise SystemExit(1 if record["status"] == "failed" else 0)
