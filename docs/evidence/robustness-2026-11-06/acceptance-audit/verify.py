"""File-only acceptance audit; preserve originals and never rerun inference."""
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
evidence = project / "docs/evidence/robustness-2026-11-06"
load = lambda path: json.loads(Path(path).read_text())


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1048576), b""):
            value.update(chunk)
    return value.hexdigest()


formal = load(evidence / "model-preservation/source-sha256.json")
assert len(formal) == 96
assert all(sha(project / name) == digest for name, digest in formal.items())
preserved = load(base.parent / "model-preservation-20261107/fixed-result.json")
assert len(preserved["source_files_before"]) == 568
assert len(preserved["frozen_before"]) == 8
for key in ["source_files_before", "frozen_before"]:
    assert all(sha(name) == digest for name, digest in preserved[key].items())
protected = load(base.parent / "power-kernel-20261107/generation-build/build.json")["preserved_input_sha256"]
assert len(protected) == 1357
assert all(sha(name) == digest for name, digest in protected.items())
checks = load(evidence / "model-preservation/checks.json")
assert checks["exit_code"] == 0 and sha(checks["log"]) == checks["log_sha256"]
assert [checks[name] for name in ["native_contracts", "active_tools_passed", "native_view_renders"]] == [36, 44, 14]
assert all(checks[name] == 0 for name in ["native_failures", "native_skipped", "tool_failures", "tool_skipped"])
native = load(evidence / "model-preservation/independent-verification.json")
assert native["status"] == "verified_native_run" and native["native_exit_code"] == 0
assert native["same_m1_old_helper_raw_matches"] == native["corpus_count"] == 100
helper = base.parent / "model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app/Contents/Helpers/CyberAudioService"
assert sha(helper) == "46083a4f7ba269b7f28377bd9532ede44ac0a812a55a55d1e1f0a9bd225cf86d"
strict = load(evidence / "retry-and-matrix/m4-strict-repeat-failed.json")
assert strict["runner"]["exit_code"] == 1
assert strict["soak"]["cycles"] == 1229
assert strict["soak"]["duration_target_seconds"] == 7200
segmentation = load(evidence / "asr-segmentation/summary.json")
assert not segmentation["pilot_passed"] and not segmentation["candidate_promoted"]

desktop = load(base / "desktop-capabilities.json")
assert desktop["screen_lock_key_present"] and desktop["screen_locked"]
assert not desktop["requested_permissions"] and not desktop["activated_application"]
signing = load(base / "signing-inventory.json")
assert signing["source_sha256"] == sha(base / "signing-inventory.py")
assert signing["developer_id_application_count"] == 0
access = load(base / "source-access.json")
assert access["source_sha256"] == sha(base / "source-access.py")
assert not access["tls_bypassed"] and not access["proxy_configuration_changed"]
assert all(row.get("http_status") != 200 for row in access["results"])
m4 = load(base / "m4-refresh.json")
assert m4["launcher_source_sha256"] == sha(base / "refresh-m4.py")
assert m4["remote_source_sha256"] == sha(base / "remote-range-refresh.py")
assert m4["status"] == "probe_failed"
assert m4["probe"]["error_domain"] == "NSURLErrorDomain" and m4["probe"]["error_code"] == -1200
assert all(m4[name] for name in ["production_preserved", "assets_preserved", "owned_processes_absent"])
assert not any(m4[name] for name in ["new_full_download_started", "model_loaded", "tls_configuration_changed"])
models = {name: row for name, row in m4["assets_after"].items() if name.startswith("models/")}
assert len(models) == 14 and sum(row["bytes"] for row in models.values()) == 2469536771
partial = [row for name, row in m4["assets_after"].items() if name.endswith(".partial")]
assert len(partial) == 1 and partial[0]["bytes"] == 117440512

reference = load(base.parent / "asr-segmentation-20261107/runner.json")["production_after"]
production = Path.home() / "Library/Application Support/CyberAudio"
with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
    state = json.load(response)
current = {
    "instance": state["instance"], "status": state["status"], "busy": state["busy"],
    "config_sha256": sha(production / "config/service.json"),
    "token_sha256": sha(production / "config/api-token"),
}
assert current == reference
last_report = load(evidence / "asr-segmentation/native-http/reports/asr-followup.json")
with socket.socket() as probe:
    assert probe.connect_ex(("127.0.0.1", last_report["port"])) != 0
assert not list((base.parent / "asr-segmentation-20261107/native-http/tmp").glob("request-*"))
rows = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True).splitlines()
owned = [row for row in rows if "CyberAudioService" in row and "/.context/robustness/" in row]
assert not owned
for path in base.glob("*.py"):
    ast.parse(path.read_text())
result = {
    "status": "verified_acceptance_incomplete",
    "verified_at": datetime.now(timezone.utc).isoformat(),
    "formal_source_hashes_verified": len(formal),
    "original_model_files_verified": len(preserved["source_files_before"]),
    "frozen_files_verified": len(preserved["frozen_before"]),
    "protected_inputs_verified": len(protected),
    "last_complete_check": {name: checks[name] for name in ["finished", "exit_code", "native_contracts", "active_tools_passed", "native_view_renders", "log_sha256"]},
    "last_native_acceptance_verified": True,
    "same_m1_old_new_raw_asr_matches": 100,
    "formal_source_changed": False,
    "inference_rerun": False,
    "download_restarted": False,
    "local_production_preserved": True,
    "local_validation_processes_absent": True,
    "latest_validation_listener_closed": True,
    "latest_request_directory_empty": True,
    "m4_production_preserved": True,
    "m4_assets_preserved_at_probe": True,
    "m4_owned_processes_absent_at_probe": True,
    "m4_final_files": 14,
    "m4_final_bytes": 2469536771,
    "m4_partial_bytes": 117440512,
    "remaining_required_gates": [
        "Original strict M4 7200-second mixed workload; repeat failed at cycle 1230",
        "Natural code-switch completeness and entity/numeric content quality",
        "Eric/dylan English numeric pronunciation and human listening",
        "Complete 23-file public download, all final hashes and fresh-root inference",
        "Pristine FFmpeg installation",
        "Real cross-app dictation, IME, focus, secure fields, clipboard and sleep recovery",
        "Physical microphone, output switching and human listening",
        "Developer ID notarization, GUI update and actual permission retention",
        "Remote CI after authorized branch delivery",
    ],
    "user_physical_participation_deferred": True,
    "overall_goal_complete": False,
    "probe_scope_limit": "Fresh process and device observations are point-in-time; reuse of verified prior tests is not a new inference or physical acceptance run",
    "source_sha256": sha(Path(__file__)),
}
target = base / "verification.json"
assert not target.exists()
target.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
