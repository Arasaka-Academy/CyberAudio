"""Independent final file/report verification only; never rerun inference."""
from datetime import datetime, timezone
from pathlib import Path
import ast
import hashlib
import json
import socket
import subprocess
import urllib.request

base = Path(__file__).resolve().parent
project = base.parents[2]
load = lambda path: json.loads(Path(path).read_text())


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1048576), b""):
            digest.update(chunk)
    return digest.hexdigest()


checked = load(base / "full-check-repaired.json")
assert checked["status"] == "passed" and checked["exit_code"] == 0
assert checked["log_sha256"] == sha(base / "full-check-repaired.log")
log = (base / "full-check-repaired.log").read_text()
setup = load(base / "full-check.json")
assert setup["status"] == "failed" and setup["exit_code"] == 2
assert setup["log_sha256"] == sha(base / "full-check.log")
assert "36 passed, 8 errors" in (base / "full-check.log").read_text()
interrupted = load(base / "full-check-fixed.json")
assert interrupted["exit_code"] == -15
assert interrupted["log_sha256"] == sha(base / "full-check-fixed.log")
repair = load(base / "dependency-object-path-repair.json")
assert repair["status"] == "repaired_private_metadata"
assert repair["failed_and_interrupted_attempts_are_not_behavioral_red"]
assert repair["original_checkout_metadata_preserved"] and repair["private_metadata_mode_restored"]
assert not repair["model_load_or_inference"]
for text in [
    "Native contracts: 36 cases, 0 failures, 0 skipped", "44 passed in",
    "WorkspaceLayout: 7 workspace states, 3 settings tabs and 4 preparation states rendered at minimum size",
    "SpeechRequestStatus: 9 real HTTP/identity/compatibility/deadline/cancellation cases passed",
]:
    assert text in log, text
for code in ["generation_limit", "inference_timeout", "inference_failed"]:
    assert f"AudioWorkflow late {code}: safe WAV/PCM reason preserved; partial audio rejected; update wait drained" in log
red = load(base / "red-behavior-ui.json")
assert red["exit_code"] == 133 and red["log_sha256"] == sha(base / "red-behavior-ui.log")
assert "Late generation_limit must preserve the WAV server reason" in (base / "red-behavior-ui.log").read_text()
red_native = load(base / "red-behavior-native.json")
assert red_native["exit_code"] == 1 and red_native["log_sha256"] == sha(base / "red-behavior-native.log")
assert "36 cases, 1 failures, 0 skipped" in (base / "red-behavior-native.log").read_text()
assert "prior_dependency_and_cache_errors_are_not_behavioral_red" in (base / "red-confirmation.json").read_text()

candidate = base / "final-bytes-stream-failure"
build = load(candidate / "build.json")
native = load(candidate / "independent-verification.json")
assert native["status"] == "verified_native_run" and native["native_exit_code"] == 0
assert native["same_m1_old_helper_raw_matches"] == native["corpus_count"] == 100
source = load(base / "source-before-full-result.json")
assert len(source) == 97 and source == build["source_sha256"]
assert all(sha(project / path) == digest for path, digest in source.items())
previous = load(project / "docs/evidence/robustness-2026-11-06/model-preservation/source-sha256.json")
assert len(previous) == 96
assert set(source) - set(previous) == {"tests/SpeechRequestStatusTests.swift"}
assert {path for path, digest in previous.items() if source[path] != digest} == {
    "Native/Sources/CyberAudioCore/Coordinator.swift", "Native/Sources/CyberAudioFixture/Main.swift",
    "Native/Tests/CyberAudioCoreTests/ServiceTests.swift", "Sources/CyberAudio/AudioWorkspace.swift",
    "Sources/CyberAudio/BackendController.swift", "tests/AudioWorkflowTests.swift", "scripts/test_dictation.sh",
}
sdk = base / "native-build/checkouts/mlx-audio-swift"
assert all(sha(sdk / path) == digest for path, digest in repair["sdk_working_source_preserved"].items())
assert sha(sdk / "Sources/MLXAudioTTS/Models/Qwen3TTS/Qwen3TTS.swift") == "17301a083f323673bc548aff031b83d9df60b2c04715af87592b1c68d42bd48a"
assert all(sha(candidate / "CyberAudio.app" / path) == row["sha256"] for path, row in build["files"].items())

legacy = load(base / "legacy-client.json")
assert legacy["status"] == "passed" and legacy["exit_code"] == 0
assert all(legacy[key] for key in ["production_preserved", "original_helper_preserved", "owned_processes_absent", "listener_closed"])
assert sha(base.parent / "m4-acceptance-20261106/seeded-service-build/CyberAudioSeededService") == legacy["original_helper_sha256"]
client = load(base / "legacy-model-data/client-result.json")
assert legacy["client"] == client
assert client["failure_code"] == "generation_limit" and client["legacy_message_field_absent"]
assert client["partial_audio_export_rejected"] and client["observed_actual_playback"]
assert client["same_instance_recovery_wav_bytes"] > 44
assert not client["physical_or_listening_acceptance"]
assert sha(base / "legacy-model-data/config/service.json") == legacy["isolated_config_sha256"]
assert sha(base / "legacy-model-data/config/api-token") == legacy["isolated_token_sha256"]
assert not list((base / "legacy-model-data/tmp").glob("request-*"))

preserved = load(base.parent / "model-preservation-20261107/fixed-result.json")
for key in ["source_files_before", "frozen_before"]:
    assert all(sha(path) == digest for path, digest in preserved[key].items())
protected = load(base.parent / "power-kernel-20261107/generation-build/build.json")["preserved_input_sha256"]
assert all(sha(path) == digest for path, digest in protected.items())
production = Path.home() / "Library/Application Support/CyberAudio"
with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
    state = json.load(response)
snapshot = {"instance": state["instance"], "status": state["status"], "busy": state["busy"],
            "config_sha256": sha(production / "config/service.json"), "token_sha256": sha(production / "config/api-token")}
assert snapshot == load(base / "production-before.json") == legacy["production_before"] == legacy["production_after"]
rows = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True).splitlines()
assert not [row for row in rows if "CyberAudioService" in row and "/.context/robustness/" in row]
for port in [18895, 18896]:
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0
for path in base.glob("*.py"):
    ast.parse(path.read_text())

result = {
    "status": "verified", "verified_at": datetime.now(timezone.utc).isoformat(),
    "source_commit": build["source_commit"], "formal_source_hashes_verified": len(source),
    "original_model_files_verified": len(preserved["source_files_before"]),
    "frozen_files_verified": len(preserved["frozen_before"]), "protected_inputs_verified": len(protected),
    "full_check_exit_code": 0, "native_contracts": 36, "active_tools_passed": 44,
    "native_view_renders": 14, "status_transport_cases": 9, "real_http_late_failure_cases": 3,
    "behavioral_red_before_fix": True, "setup_errors_not_counted_as_red": True,
    "full_check_setup_failure_preserved": True, "premature_retry_interruption_preserved": True,
    "private_sdk_metadata_repair_only": True,
    "original_96_source_delta_verified": True, "managed_sdk_sources_preserved": True,
    "actual_final_app_native_acceptance_passed": True, "same_m1_old_new_raw_asr_matches": 100,
    "legacy_actual_model_failure_reason_recovered": True, "legacy_failure_code": "generation_limit",
    "legacy_partial_wav_rejected": True, "legacy_same_instance_recovery": True,
    "model_loaded_by_verifier": False, "inference_rerun_by_verifier": False,
    "production_preserved": True, "owned_model_processes_absent": True, "listeners_closed": True,
    "default_sampling_and_inference_source_changed": False, "overall_goal_complete": False,
    "remaining_gates": ["Strict original M4 7200s workload", "Natural mixed-speech and numeric/entity content quality",
                        "Physical microphone/listening/device/sleep and cross-app dictation", "Full public download/fresh installation",
                        "Developer ID/notarization/real GUI upgrade", "Remote CI after authorized delivery"],
    "source_sha256": sha(Path(__file__)),
}
target = base / "verification.json"
assert not target.exists()
target.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
