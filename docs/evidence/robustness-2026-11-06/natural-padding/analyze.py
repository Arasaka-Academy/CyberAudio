"""Independently verify the frozen short-audio placement pilot from saved files."""
import hashlib
import importlib.util
import json
import os
import socket
import urllib.request
import wave
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
DATA = ROOT.parent / "post-delivery-20261107"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def edit_distance(left, right):
    # A separate full matrix rather than the retained scorer's rolling rows.
    matrix = [[0] * (len(right) + 1) for _ in range(len(left) + 1)]
    for i in range(len(left) + 1):
        matrix[i][0] = i
    for j in range(len(right) + 1):
        matrix[0][j] = j
    for i, a in enumerate(left, 1):
        for j, b in enumerate(right, 1):
            matrix[i][j] = min(matrix[i - 1][j] + 1, matrix[i][j - 1] + 1,
                               matrix[i - 1][j - 1] + (a != b))
    return matrix[-1][-1]


plan = json.loads((ROOT / "protocol.json").read_text())
runner = json.loads((ROOT / "native-http/runner.json").read_text())
assert runner["status"] == "whole_test_transcribed_not_quality_accepted"
assert runner["completed_requests"] == plan["expected_requests"] == 21
assert runner["study_protocol_sha256"] == sha(ROOT / "protocol.json")
assert runner["parity_counts"] == {"original": 7, "trailing": 7}
assert runner["owned_helper_exit_code"] == 0
assert all(runner[key] for key in ["production_preserved", "source_preserved", "protected_file_metadata_preserved", "owned_listener_closed"])
assert runner["study_files_sha256"] == {name: sha(ROOT / name) for name in runner["study_files_sha256"]}
assert plan["driver_files_sha256"] == {name: sha(ROOT / name) for name in plan["driver_files_sha256"]}
assert sha(plan["sdk_source"]) == plan["sdk_source_sha256"]
assert sha(DATA / "natural-baseline/results.jsonl") == plan["native_baseline_sha256"]
assert sha(DATA / "ascend-private/test-manifest.json") == plan["manifest_sha256"]
manifest = json.loads((DATA / "ascend-private/test-manifest.json").read_text())
lookup = {row["id"]: row for row in manifest}
selected = {plan["fixed_problem_id"]}
eligible = [row for row in manifest if row["frames"] < 16000]
assert len(eligible) == plan["eligible_count"]
for language in ["en", "mixed", "zh"]:
    rows = sorted((row for row in eligible if row["language"] == language and row["id"] != plan["fixed_problem_id"]), key=lambda row: row["id"])
    selected.update([rows[0]["id"], rows[-1]["id"]])
assert {case["source_id"] for case in plan["cases"]} == selected
originals = {row["id"]: row["response"] for row in map(json.loads, (DATA / "natural-baseline/results.jsonl").read_text().splitlines())}
results = list(map(json.loads, (ROOT / "native-http/results.jsonl").read_text().splitlines()))
assert [row["id"] for row in results] == [row["id"] for row in plan["cases"]]
by_source = {}
for case, result in zip(plan["cases"], results):
    source = DATA / "ascend-private" / lookup[case["source_id"]]["audio"]
    assert sha(source) == case["original_audio_sha256"]
    assert sha(case["audio"]) == case["audio_sha256"]
    with wave.open(str(source), "rb") as sound:
        original = sound.readframes(sound.getnframes())
    with wave.open(case["audio"], "rb") as sound:
        assert (sound.getnchannels(), sound.getsampwidth(), sound.getframerate(), sound.getnframes()) == (1, 2, 16000, case["frames"])
        pcm = sound.readframes(sound.getnframes())
    assert hashlib.sha256(pcm).hexdigest() == case["pcm_sha256"]
    zeros = bytes(2 * (16000 - case["original_frames"]))
    assert pcm == {"original": original, "trailing": original + zeros, "leading": zeros + original}[case["arm"]]
    assert result["http_status"] == 200
    response = result["response"]
    assert abs(response["duration"] - case["frames"] / 16000) < 1e-8
    if case["arm"] in ["original", "trailing"]:
        keys = ["text", "language", "no_speech", "model", "repository", "revision"]
        assert {key: response[key] for key in keys} == {key: originals[case["source_id"]][key] for key in keys}
    by_source.setdefault(case["source_id"], {})[case["arm"]] = response
spec = importlib.util.spec_from_file_location("frozen_padding_scorer", DATA / "score-natural-asr.py")
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)
scorer.self_test()
comparisons = []
for source_id, responses in by_source.items():
    reference = scorer.mixed_tokens(lookup[source_id]["reference"])
    counts = {}
    for arm in ["original", "trailing", "leading"]:
        tokens = scorer.mixed_tokens(responses[arm]["text"])
        counts[arm] = edit_distance(reference, tokens)
        assert counts[arm] == scorer.distance(reference, tokens)
    comparisons.append({"id": source_id, "role": "retained_empty_case" if source_id == plan["fixed_problem_id"] else "metadata_selected_short_control",
                        "reference": lookup[source_id]["reference"], "metadata_language": lookup[source_id]["language"],
                        "frames": lookup[source_id]["frames"], "reference_tokens": len(reference),
                        "responses": {arm: {key: responses[arm][key] for key in ["text", "language", "no_speech"]} for arm in responses},
                        "edits": counts,
                        "leading_normalized_exact": scorer.mixed_tokens(responses["leading"]["text"]) == reference,
                        "new_empty": bool(scorer.mixed_tokens(responses["original"]["text"])) and not scorer.mixed_tokens(responses["leading"]["text"])})
source_hashes = json.loads((ROOT / "native-http/source-before.json").read_text())
assert all(sha(PROJECT / relative) == digest for relative, digest in source_hashes.items())
metadata = json.loads((ROOT.parent / "natural-reference-20261107-complete/protected-metadata-before.private.json").read_text())
assert all([Path(path).stat().st_size, Path(path).stat().st_mtime_ns] == value for path, value in metadata.items())
build = json.loads((Path(plan["app"]).parent / "build.json").read_text())
assert sha(Path(plan["app"]).parent / "build.json") == plan["app_build_sha256"]
assert all(sha(PROJECT / relative) == digest for relative, digest in build["frozen_sha256"].items())
assert all(sha(Path(plan["app"]) / relative) == row["sha256"] for relative, row in build["files"].items())
try:
    os.kill(runner["owned_helper_pid"], 0)
except ProcessLookupError:
    pass
else:
    raise AssertionError("Owned Helper remains alive")
with socket.socket() as listener:
    assert listener.connect_ex(("127.0.0.1", plan["execution"]["port"])) != 0
assert not list((ROOT / "native-http/service/tmp").glob("request-*"))
with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
    production = json.load(response)
assert production["instance"] == runner["production_before"]["instance"]
for name, field in [("service.json", "config_sha256"), ("api-token", "token_sha256")]:
    assert sha(Path.home() / "Library/Application Support/CyberAudio/config" / name) == runner["production_before"][field]
problem = next(row for row in comparisons if row["id"] == plan["fixed_problem_id"])
controls = [row for row in comparisons if row["role"] != "retained_empty_case"]
problem_passed = problem["leading_normalized_exact"]
controls_passed = all(row["edits"]["leading"] <= row["edits"]["original"] and not row["new_empty"] for row in controls)
summary = {"status": "silence_placement_pilot_verified_not_product_accepted", "verified_at": datetime.now(timezone.utc).isoformat(),
           "protocol_sha256": sha(ROOT / "protocol.json"), "requests": 21, "baseline_parity": {"original": 7, "trailing": 7},
           "verified_pcm_and_ffmpeg_cases": 21, "source_files_verified": len(source_hashes), "protected_metadata_verified": len(metadata),
           "frozen_artifacts_verified": len(build["frozen_sha256"]), "app_files_verified": len(build["files"]),
           "edits": {arm: sum(row["edits"][arm] for row in comparisons) for arm in ["original", "trailing", "leading"]},
           "leading_improves": sum(row["edits"]["leading"] < row["edits"]["original"] for row in comparisons),
           "leading_worsens": sum(row["edits"]["leading"] > row["edits"]["original"] for row in comparisons),
           "leading_ties": sum(row["edits"]["leading"] == row["edits"]["original"] for row in comparisons),
           "problem_gate_passed": problem_passed, "control_gate_passed": controls_passed,
           "pilot_qualified_for_future_independent_evaluation": bool(problem_passed and controls_passed),
           "production_preserved": True, "owned_helper_absent": True, "owned_listener_closed": True, "temporary_request_count": 0,
           "candidate_promoted": False, "original_gates_preserved": True, "cases": comparisons,
           "limits": plan["limits"], "data_license": plan["data_license"], "data_attribution": plan["data_attribution"]}
with (ROOT / "summary.private.json").open("x") as stream:
    json.dump(summary, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
print(json.dumps({key: value for key, value in summary.items() if key not in ["cases", "limits"]}, indent=2))
