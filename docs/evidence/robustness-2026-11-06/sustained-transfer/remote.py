"""Four fixed normal-TLS ranges over one owned SSH connection; no asset writes."""
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

TARGET = Path.home() / "Library/Caches/CyberAudio/RobustnessValidation/20261106-e2f34df-target1"
ROOT = TARGET / "sustained-transfer-20261107"
DATA = TARGET / "fresh-download"
PRODUCTION = Path.home() / "Library/Application Support/CyberAudio"
ROOT.mkdir(mode=0o700, exist_ok=False)
record = {"status": "preflight", "started_at": datetime.now(timezone.utc).isoformat(), "rounds": [],
          "model_loaded": False, "download_cli_started": False, "asset_bytes_written": False,
          "tls_validation": "default", "maximum_rounds": 4, "requested_bytes_each": 8388608}
began = time.monotonic()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def snapshot():
    with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
        state = json.load(response)
    return {"instance": state["instance"], "status": state["status"], "busy": state["busy"],
            "config_sha256": sha(PRODUCTION / "config/service.json"), "token_sha256": sha(PRODUCTION / "config/api-token")}


def inventory():
    return {str(path.relative_to(DATA)): [path.stat().st_size, path.stat().st_mtime_ns]
            for path in DATA.rglob("*") if path.is_file()}


def save():
    record["elapsed_seconds"] = round(time.monotonic() - began, 3)
    (ROOT / "result.json").write_text(json.dumps(record, indent=2) + "\n")


def interrupted(*_):
    raise RuntimeError("Bounded sustained probe interrupted; no retry")


signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGALRM, interrupted)
signal.alarm(480)
try:
    record["production_before"] = snapshot()
    assert record["production_before"]["status"] == "ready" and not record["production_before"]["busy"]
    record["assets_before"] = inventory()
    partials = list((DATA / "tmp/downloads").glob("*.partial"))
    assert len(partials) == 1 and partials[0].stat().st_size == 184549376
    record["partial_sha256_before"] = sha(partials[0])
    record["partial_bytes"] = partials[0].stat().st_size
    processes = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True)
    assert not any(str(TARGET) in line and ("CyberAudioAssets download" in line or "CyberAudioService --root" in line) for line in processes.splitlines())
    bindings = []
    for family, address in [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET, "127.0.0.2"), (socket.AF_INET6, "::1")]:
        with socket.socket(family, socket.SOCK_STREAM) as stream:
            stream.settimeout(1)
            bindings.append({"address": address, "errno": stream.connect_ex((address, 17891))})
    record["bindings"] = bindings
    assert bindings[0]["errno"] == 0 and all(row["errno"] != 0 for row in bindings[1:])
    owner = subprocess.run(["/usr/sbin/lsof", "-nP", "-iTCP:17891", "-sTCP:LISTEN", "-Fp"], capture_output=True, text=True, timeout=10)
    pids = [int(line[1:]) for line in owner.stdout.splitlines() if line.startswith("p")]
    assert len(pids) == 1
    record["forward_owner_pid"] = pids[0]
    record["forward_owner_identity"] = subprocess.check_output(["/bin/ps", "-p", str(pids[0]), "-o", "uid=,lstart=,command="], text=True).strip()
    lock = json.loads((TARGET / "Models.lock.json").read_text())
    config = json.loads((DATA / "config/service.json").read_text())
    final_bytes = 0
    final_count = 0
    for entry in lock["files"]:
        model, relative = entry["path"].split("/", 1)
        path = Path(config[model + "_dir"]) / relative
        if path.exists():
            assert path.stat().st_size == entry["size"] and sha(path) == entry["sha256"]
            final_count += 1
            final_bytes += entry["size"]
    assert final_count == 14 and final_bytes == 2469536771
    record["verified_final_count"], record["verified_final_bytes"] = final_count, final_bytes
    remaining = sum(entry["size"] for entry in lock["files"]) - final_bytes - record["partial_bytes"]
    record["remaining_bytes"] = remaining
    record["minimum_active_bytes_per_second"] = remaining * 1.5 / 3600
    source = ROOT / "RangeProbe.swift"
    assert source.exists()
    record["probe_source_sha256"] = sha(source)
    build = subprocess.run(["/usr/bin/swiftc", "-parse-as-library", "-target", "arm64-apple-macos26.0", str(source), "-o", str(ROOT / "probe")], capture_output=True, text=True, timeout=90)
    (ROOT / "compile.private.log").write_text(build.stdout + build.stderr)
    assert build.returncode == 0
    record["probe_binary_sha256"] = sha(ROOT / "probe")
    production_config = json.loads((PRODUCTION / "config/service.json").read_text())
    original = Path(production_config["tts_dir"]) / "model.safetensors"
    entry = next(row for row in lock["files"] if row["path"] == "tts/model.safetensors")
    assert original.stat().st_size == entry["size"] and sha(original) == entry["sha256"]
    record["reference_model_full_digest_verified"] = True
    record["status"] = "probing"
    save()
    first_start = time.monotonic()
    for index in range(4):
        due = first_start + 45 * index
        while time.monotonic() < due:
            time.sleep(min(3, due - time.monotonic()))
        assert snapshot() == record["production_before"], "Production changed or became busy"
        offset = record["partial_bytes"] + index * 8388608
        with original.open("rb") as stream:
            stream.seek(offset)
            expected = hashlib.sha256(stream.read(8388608)).hexdigest()
        run = subprocess.run([str(ROOT / "probe"), str(offset), "8388608"], capture_output=True, text=True, timeout=55)
        with (ROOT / ("round-" + str(index) + ".private.json")).open("x") as stream:
            stream.write(run.stdout)
        assert run.returncode == 0
        observed = json.loads(run.stdout)
        passed = (observed.get("status") == 206 and observed.get("bytes") == 8388608 and observed.get("sha256") == expected
                  and observed.get("content_range") == f"bytes {offset}-{offset + 8388608 - 1}/{entry['size']}")
        observed.update(index=index, expected_sha256=expected, normal_tls_exact_range_passed=passed)
        record["rounds"].append(observed)
        save()
        print(json.dumps({"event": "sustained_range", **observed}), flush=True)
        assert passed, "A fixed sustained Range failed; stop without full resume"
    active_seconds = sum(row["elapsed_seconds"] for row in record["rounds"])
    rate = 4 * 8388608 / active_seconds
    record["active_transfer_seconds"] = active_seconds
    record["active_bytes_per_second"] = rate
    record["projected_remaining_seconds_at_active_rate"] = remaining / rate
    record["full_resume_admitted"] = rate >= record["minimum_active_bytes_per_second"]
    record["status"] = "sustained_route_qualified" if record["full_resume_admitted"] else "sustained_route_too_slow"
except BaseException as error:
    record.update(status="failed", error_type=type(error).__name__, error=str(error), full_resume_admitted=False)
finally:
    signal.alarm(0)
    try:
        record["production_after"] = snapshot()
        record["production_preserved"] = record["production_after"] == record["production_before"]
        record["assets_after"] = inventory()
        record["assets_preserved"] = record["assets_before"] == record["assets_after"]
        record["partial_sha256_after"] = sha(partials[0])
        record["partial_preserved"] = record["partial_sha256_after"] == record["partial_sha256_before"]
        assert record["production_preserved"] and record["assets_preserved"] and record["partial_preserved"]
    except Exception as error:
        record.update(status="failed", preservation_error_type=type(error).__name__, full_resume_admitted=False)
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
print(json.dumps({"event": "terminal", "record": record}), flush=True)
