"""Independently validate completed native evidence; do not rerun model workload."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import socket
import subprocess
import sys
import urllib.request
import wave

base = Path(__file__).resolve().parent
project = base.parents[2]
label = sys.argv[1] if len(sys.argv) == 2 else "final-bytes-939050f"
assert label in {"final-bytes-939050f", "final-bytes-small-range", "final-bytes-language-routing"}
candidate = base / label
root = candidate / "data-full"
app = candidate / "CyberAudio.app"
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
read = lambda path: json.loads(path.read_text())
build = read(candidate / "build.json")
receipt = read(candidate / "full-result.json")
native = read(root / "reports/native-service.json")
assert receipt["exit_code"] == 0 and receipt["native_report_status"] == native["status"] == "passed"
if receipt["status"] == "failed":
    assert receipt["error_type"] == "KeyError" and receipt["error"] == "'tts'"
else:
    assert receipt["status"] == "passed"
assert all(receipt[key] for key in ["production_preserved", "frozen_preserved", "candidate_bytes_preserved", "owned_listener_closed"])
assert sha(candidate / "verify-native") == build["driver_sha256"] == receipt["driver_sha256"]
assert native["helper_sha256"] == build["files"]["Contents/Helpers/CyberAudioService"]["sha256"]
for relative, value in build["files"].items():
    assert sha(app / relative) == value["sha256"]
for relative, digest in build["frozen_sha256"].items():
    assert sha(project / relative) == digest
subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True, timeout=120)
rows = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,lstart=,command="], capture_output=True, text=True, check=True).stdout.splitlines()
assert not [row for row in rows if str(candidate.resolve()) in row and any(name in row for name in ["/verify-native ", "/CyberAudio.app/Contents/MacOS/", "/CyberAudio.app/Contents/Helpers/"])]
with socket.socket() as probe:
    assert probe.connect_ex(("127.0.0.1", receipt["port"])) != 0
assert not list((root / "tmp").glob("request-*"))
assert native["production_service_preserved"] and native["outbound_denied"] and native["single_owner_lock"]
assert len(native["stt_formats"]) == 16 and len(native["asr_corpus"]) == 100
assert len(native["tts_voices"]) == 9 and len(native["stream_disconnect_recovery"]) == 3
assert all(row["status"] == "cancelled" and row["recovery_http_status"] == 200 for row in native["stream_disconnect_recovery"])
assert native["stream_complete"]["bytes"] > 0 and native["stream_complete"]["bytes"] % 2 == 0
assert 0 < native["stdin_eof_stop_ms"] < 12000
assert {row["signal"] for row in native["app_active_exit_and_restart"]} == {9, 15}
assert all(row["active_inference"] and row["helper_gone"] for row in native["app_active_exit_and_restart"])
wavs = []
for row in native["tts_voices"]:
    path = root / "reports" / (row["voice"] + ".wav")
    with wave.open(str(path), "rb") as audio:
        assert audio.getnchannels() == 1 and audio.getsampwidth() == 2 and audio.getframerate() == 24000
        assert len(audio.readframes(audio.getnframes())) == audio.getnframes() * 2
        wavs.append({"voice": row["voice"], "frames": audio.getnframes(), "sha256": sha(path)})
manifest = read(base / "corpus/corpus.json")
by_id = {row["id"]: row for row in manifest}
old_rows = read(base / "installed-m1-baseline/reports/asr-baseline.json")["results"]
old = {row["id"]: row for row in old_rows}
differences = []
for row in native["asr_corpus"]:
    original = by_id[row["id"]]
    assert row["reference"] == original["reference"] == old[row["id"]]["reference"]
    assert row["language"] == original["language"] == old[row["id"]]["language"]
    assert row["audio_sha256"] == original["audio_sha256"] == old[row["id"]]["audio_sha256"] == sha(base / "corpus" / original["audio"])
    if row["result"]["text"] != old[row["id"]]["text"]:
        differences.append({"id": row["id"], "before": old[row["id"]]["text"], "after": row["result"]["text"]})
production = Path.home() / "Library/Application Support/CyberAudio"
with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
    state = json.load(response)
current = {"instance": state["instance"], "status": state["status"], "config_sha256": sha(production / "config/service.json"), "token_sha256": sha(production / "config/api-token")}
assert current == receipt["production_before"] == receipt["production_after"]
result = {"status": "verified_native_run", "verified_at": datetime.now(timezone.utc).isoformat(),
          "source_commit": build["source_commit"], "native_exit_code": 0, "native_status": "passed",
          "collector_status": receipt["status"], "collector_error": receipt.get("error"),
          "collector_failure_preserved": receipt["status"] == "failed", "workload_rerun": False,
          "scope": "Read-only independent verification of native report, final bytes, all corpus hashes, all nine WAV frames, cancellation/lifecycle, process cleanup, frozen artifacts and production preservation",
          "original_runner_sha256": sha(candidate / "run-final-candidate-launched.py"),
          "corrected_runner_sha256": sha(base / "run-final-candidate.py"),
          "raw_result_sha256": sha(candidate / "full-result.json"), "native_report_sha256": sha(root / "reports/native-service.json"),
          "build_sha256": sha(candidate / "build.json"), "native_duration_seconds": receipt["elapsed_seconds"],
          "same_m1_old_helper_raw_matches": 100 - len(differences), "corpus_count": 100,
          "same_audio_hashes_references_languages": True, "differences": differences, "wav_verification": wavs,
          "strict_nested_signatures": True, "owned_processes_absent": True, "owned_listener_closed": True,
          "temporary_request_count": 0, "frozen_preserved": True, "candidate_bytes_preserved": True, "production_preserved": True,
          "limitations": ["This is the completed local M1 full gate, not the failed strict M4 soak or public download gate", "No physical microphone/listening/unplug/sleep, interactive dictation or notarized GUI upgrade acceptance"]}
output = candidate / "independent-verification.json"
assert not output.exists()
output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({key: result[key] for key in ["status", "native_exit_code", "collector_status", "workload_rerun", "same_m1_old_helper_raw_matches", "owned_processes_absent", "production_preserved"]}, indent=2))
