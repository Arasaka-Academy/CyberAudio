"""Read back every fixed complete TTS artifact after the necessary generation gate."""
import hashlib
import json
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

STUDY = Path(__file__).resolve().parent
BASE = STUDY.parent / "post-delivery-20261107"
STUDY_PROTOCOL = json.loads((STUDY / "protocol.json").read_text())
PROJECT = BASE.parents[2]
PROTOCOL = json.loads((BASE / "natural-asr-protocol.json").read_text())
ASR_EXEC = json.loads((STUDY / "asr-execution-protocol.json").read_text())
APP = Path(ASR_EXEC["app"])
BUILD = json.loads((APP.parent / "build.private.json").read_text())
HELPER = APP / "Contents/Helpers/CyberAudioService"
ROOT = STUDY / "roundtrip-current-http"
PORT = STUDY_PROTOCOL["execution"]["port"]
SERVICE = ROOT / "service"
PRODUCTION = Path.home() / "Library/Application Support/CyberAudio"
token = ""
process = None
baseline_sources = {}
protected_stat = {}
report = {"status": "preflight", "started_at": datetime.now(timezone.utc).isoformat(), "completed_requests": 0}
began = time.monotonic()


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def save():
    report["elapsed_seconds"] = round(time.monotonic() - began, 3)
    path = ROOT / "runner.json"
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(report, indent=2) + "\n")
    temp.replace(path)


def fetch(path, data=None, content_type=None, production=False, timeout=150):
    headers = {} if production else {"Authorization": "Bearer " + token}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(
        f"http://127.0.0.1:{8765 if production else PORT}" + path,
        headers=headers, data=data,
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


def production_snapshot():
    status, state = fetch("/ready", production=True, timeout=5)
    assert status == 200 and state["status"] == "ready"
    return {
        "instance": state["instance"], "ready": True,
        "config_sha256": sha(PRODUCTION / "config/service.json"),
        "token_sha256": sha(PRODUCTION / "config/api-token"),
    }, state.get("busy", False)


def source_snapshot():
    paths = subprocess.check_output(
        ["rtk", "proxy", "git", "ls-files", "Sources", "Native", "scripts", "tests", "packaging", ".github", "Makefile", "Package.swift", "Package.resolved", "pyproject.toml", "uv.lock"],
        cwd=PROJECT, text=True,
    ).splitlines()
    return {path: sha(PROJECT / path) for path in paths}


def file_stats():
    paths = [PROJECT / relative for relative in BUILD["frozen_sha256"]]
    lock = json.loads((PROJECT / "Native/Sources/CyberAudioCore/Models.lock.json").read_text())
    for row in lock["files"]:
        paths.append((PROJECT / ".context/robustness/linked-models" / row["path"]).resolve())
    return {str(path): [path.stat().st_size, path.stat().st_mtime_ns] for path in paths}


def broad_preservation():
    expected = json.loads((STUDY.parent / "natural-reference-20261107-complete/protected-metadata-before.private.json").read_text())
    assert all([Path(p).stat().st_size, Path(p).stat().st_mtime_ns] == value for p, value in expected.items())
    return len(expected)


def interrupted(*_):
    raise RuntimeError("Natural baseline interrupted; retain the partial run and do not retry automatically")


signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)
signal.signal(signal.SIGALRM, interrupted)
ROOT.mkdir(mode=0o700, exist_ok=False)
signal.alarm(STUDY_PROTOCOL["execution"]["whole_timeout_seconds"])
try:
    assert json.loads((BASE / "ascend-private/acquisition-result.json").read_text())["verified"]
    dataset = json.loads((BASE / "ascend-private/preparation.json").read_text())
    assert dataset["status"] == "verified" and dataset["utterances"] == 1315
    manifest_path = BASE / "ascend-private/test-manifest.json"
    assert sha(manifest_path) == dataset["manifest_sha256"]
    manifest = json.loads(manifest_path.read_text())
    assert len(manifest) == len({r["id"] for r in manifest}) == 1315
    derived = json.loads((STUDY / "roundtrip-manifest.json").read_text())
    assert derived["protocol_sha256"] == sha(STUDY / "protocol.json")
    manifest = derived["cases"]
    assert len(manifest) == derived["expected_requests"]
    assert sha(STUDY / "protocol.json") == ASR_EXEC["frozen_study_protocol_sha256"]
    assert sha(STUDY / "roundtrip-manifest.json") == ASR_EXEC["manifest_sha256"]
    assert len(manifest) == ASR_EXEC["expected_requests"]
    report["study_protocol_sha256"] = sha(STUDY / "protocol.json")
    report["broad_protected_metadata_before_count"] = broad_preservation()
    report["study_files_sha256"] = {name: sha(STUDY / name) for name in ["protocol.json", "asr-execution-protocol.json", "run-current-roundtrip.py", "run-roundtrip.py", "prepare-roundtrip.py", "roundtrip-manifest.json", "roundtrip-transform.json"]}
    report["production_before"], busy = production_snapshot()
    assert not busy, "Production is in use; no evaluation was started"
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", PORT)) != 0, "Evaluation port already occupied"
    processes = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True, errors="replace").splitlines()
    for line in processes:
        if ".context/robustness/" in line and str(os.getpid()) != line.strip().split()[0]:
            assert not any(name in line for name in ["CyberAudioService", "transcribe-reference.py", "Probe --"]), "Another validation model may be active"
    assert sha(APP.parent / "build.private.json") == ASR_EXEC["build_manifest_sha256"]
    assert sha(HELPER) == ASR_EXEC["helper_sha256"] == BUILD["app_files"]["Contents/Helpers/CyberAudioService"]["sha256"]
    for relative, expected in BUILD["source_sha256"].items():
        if relative.startswith("Native/") or relative in {"scripts/patch_mlx_audio.swift", "Package.resolved"}:
            assert sha(PROJECT / relative) == expected, "Native source differs from accepted Helper: " + relative
    for relative in ["Contents/Helpers/CyberAudioAssets", "Contents/Resources/mlx.metallib", "Contents/Resources/offline.sb"]:
        assert sha(APP / relative) == BUILD["app_files"][relative]["sha256"]
    baseline_sources = source_snapshot()
    (ROOT / "source-before.json").write_text(json.dumps(baseline_sources, indent=2) + "\n")
    protected_stat = file_stats()
    report.update(
        status="starting", protocol_sha256=sha(BASE / "natural-asr-protocol.json"),
        manifest_sha256=sha(manifest_path), helper_sha256=sha(HELPER),
        runner_sha256=sha(__file__), scorer_sha256=sha(BASE / "score-natural-asr.py"),
        scorer_lock_sha256=sha(BASE / "scorer.lock"),
        helper_source_commit=ASR_EXEC["source_head"], native_sources_match_accepted_build=True,
        expected_requests=len(manifest), port=PORT, retries=0,
        protected_file_metadata_before=protected_stat,
    )
    save()
    subprocess.run(
        [str(HELPER.with_name("CyberAudioAssets")), "prepare", "--root", str(SERVICE), "--port", str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=30,
    )
    config_path = SERVICE / "config/service.json"
    config = json.loads(config_path.read_text())
    config.update(
        asr_dir=str((PROJECT / ".context/robustness/linked-models/asr").resolve()),
        tts_dir=str((PROJECT / ".context/robustness/linked-models/tts").resolve()),
        ffmpeg="/opt/homebrew/bin/ffmpeg",
    )
    config_path.write_text(json.dumps(config))
    token = (SERVICE / "config/api-token").read_text().strip()
    service_preserved = {name: sha(SERVICE / "config" / name) for name in ["service.json", "api-token"]}
    instance = uuid.uuid4().hex
    with (ROOT / "helper.private.log").open("xb") as log:
        process = subprocess.Popen(
            ["/usr/bin/sandbox-exec", "-f", str(APP / "Contents/Resources/offline.sb"), str(HELPER),
             "--root", str(SERVICE), "--parent-pid", str(os.getpid()), "--instance", instance,
             "--metallib", str(APP / "Contents/Resources/mlx.metallib")],
            stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        report["owned_helper_pid"] = process.pid
        save()
        until = time.monotonic() + 180
        while True:
            assert process.poll() is None and time.monotonic() < until, "Native Helper startup failed or timed out"
            try:
                status, state = fetch("/ready", timeout=5)
            except OSError:
                time.sleep(.2)
                continue
            assert state["status"] != "failed", "Native model load failed; see private Helper log"
            if status == 200 and state["status"] == "ready":
                assert state["instance"] == instance
                report["startup"] = state
                break
            time.sleep(.2)
        report["status"] = "transcribing"
        save()
        print("Native Helper ready; fixed complete test split:", len(manifest), flush=True)
        with (ROOT / "results.jsonl").open("x") as output:
            for i, row in enumerate(manifest, 1):
                assert time.monotonic() - began < 3600, "Original whole-run deadline reached"
                assert process.poll() is None, "Native Helper exited during the fixed run"
                audio = Path(row["audio"])
                assert sha(audio) == row["audio_sha256"]
                boundary = "natural-baseline-" + uuid.uuid4().hex
                body = (f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\n{row["request_language"]}\r\n'
                        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n\r\n').encode()
                body += audio.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
                start = time.monotonic()
                try:
                    status, response = fetch("/v1/audio/transcriptions", body, "multipart/form-data; boundary=" + boundary)
                except Exception as error:
                    result = {"id": row["id"], "transport_error_type": type(error).__name__, "elapsed_seconds": time.monotonic() - start}
                    output.write(json.dumps(result) + "\n"); output.flush()
                    raise RuntimeError("ASR transport failed; partial record retained") from error
                result = {"id": row["id"], "http_status": status, "response": response, "elapsed_seconds": time.monotonic() - start}
                output.write(json.dumps(result, ensure_ascii=False) + "\n"); output.flush()
                report["completed_requests"] = i
                save()
                assert status == 200 and isinstance(response.get("text"), str), "ASR request failed; original result retained"
                if i % 7 == 0:
                    print("Transcribed", i, "/", len(manifest), "elapsed", round(time.monotonic() - began), "s", flush=True)
                    _, busy = production_snapshot()
                    assert not busy, "Production became active; stop validation without touching production"
        assert not list((SERVICE / "tmp").glob("request-*"))
        report["temporary_request_count"] = 0
        assert service_preserved == {name: sha(SERVICE / "config" / name) for name in service_preserved}
        process.stdin.close()
        process.wait(timeout=12)
        assert process.returncode == 0, "EOF shutdown failed"
        report["owned_helper_exit_code"] = process.returncode
    report["status"] = "whole_test_transcribed_not_quality_accepted"
except BaseException as error:
    report.update(status="failed", error_type=type(error).__name__, error=str(error))
finally:
    signal.alarm(0)
    if process is not None and process.poll() is None:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=12)
                report["forced_kill"] = True
        report["owned_helper_exit_code"] = process.returncode
    try:
        report["broad_protected_metadata_after_count"] = broad_preservation()
        assert report["broad_protected_metadata_after_count"] == report["broad_protected_metadata_before_count"]
        assert report["study_files_sha256"] == {name: sha(STUDY / name) for name in report["study_files_sha256"]}
        report["production_after"], _ = production_snapshot()
        report["production_preserved"] = report["production_after"] == report.get("production_before")
        report["source_preserved"] = baseline_sources == source_snapshot() if baseline_sources else True
        report["protected_file_metadata_preserved"] = protected_stat == file_stats() if protected_stat else True
        assert report["production_preserved"] and report["source_preserved"] and report["protected_file_metadata_preserved"]
        with socket.socket() as probe:
            report["owned_listener_closed"] = probe.connect_ex(("127.0.0.1", PORT)) != 0
        assert report["owned_listener_closed"]
    except Exception as error:
        report.update(status="failed", preservation_error_type=type(error).__name__, preservation_error=str(error))
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
print(json.dumps({k: report[k] for k in ["status", "completed_requests", "elapsed_seconds", "production_preserved"] if k in report}, indent=2), flush=True)
raise SystemExit(0 if report["status"] == "whole_test_transcribed_not_quality_accepted" else 1)
