"""Verify the frozen input-level pilot without loading models or rewriting audio."""
from datetime import datetime, timezone
from pathlib import Path
import array
import hashlib
import importlib.util
import json
import os
import socket
import sys
import urllib.request
import wave

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
DATA = ROOT.parent / "post-delivery-20261107"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


plan = json.loads((ROOT / "protocol.json").read_text())
runner = json.loads((ROOT / "native-http/runner.json").read_text())
assert runner["status"] == "whole_test_transcribed_not_quality_accepted"
assert runner["study_protocol_sha256"] == sha(ROOT / "protocol.json")
assert runner["completed_requests"] == plan["expected_requests"] == 14
assert all(runner[k] for k in ["production_preserved", "source_preserved", "protected_file_metadata_preserved", "owned_listener_closed"])
assert runner["owned_helper_exit_code"] == 0
assert runner["broad_protected_metadata_before_count"] == runner["broad_protected_metadata_after_count"]
assert runner["study_files_sha256"] == {name: sha(ROOT / name) for name in runner["study_files_sha256"]}
assert sha(DATA / "natural-baseline/results.jsonl") == plan["native_baseline_sha256"]
originals = {row["id"]: row for row in map(json.loads, (DATA / "natural-baseline/results.jsonl").read_text().splitlines())}
manifest = {row["id"]: row for row in json.loads((DATA / "ascend-private/test-manifest.json").read_text())}
rows = list(map(json.loads, (ROOT / "native-http/results.jsonl").read_text().splitlines()))
assert [row["id"] for row in rows] == [case["id"] for case in plan["cases"]]
assert all(row["http_status"] == 200 and isinstance(row["response"]["text"], str) for row in rows)
spec = importlib.util.spec_from_file_location("frozen_level_scorer", DATA / "score-natural-asr.py")
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)
scorer.self_test()
comparisons = []
keys = ["text", "language", "duration", "no_speech", "model", "repository", "revision"]
for index in range(0, len(rows), 2):
    before, after = rows[index:index + 2]
    original_case, louder_case = plan["cases"][index:index + 2]
    source_id = original_case["source_id"]
    assert source_id == louder_case["source_id"]
    assert original_case["gain"] == 1 and louder_case["gain"] == 4
    expected = originals[source_id]["response"]
    assert {k: before["response"][k] for k in keys} == {k: expected[k] for k in keys}
    original_audio = DATA / "ascend-private" / manifest[source_id]["audio"]
    assert sha(original_audio) == manifest[source_id]["audio_sha256"]
    with wave.open(str(original_audio), "rb") as sound:
        original_pcm = sound.readframes(sound.getnframes())
    pcm = []
    for case in [original_case, louder_case]:
        assert sha(case["audio"]) == case["audio_sha256"]
        with wave.open(case["audio"], "rb") as sound:
            assert (sound.getnchannels(), sound.getsampwidth(), sound.getframerate(), sound.getnframes()) == (1, 2, 16000, case["frames"])
            payload = sound.readframes(sound.getnframes())
        assert hashlib.sha256(payload).hexdigest() == case["pcm_sha256"]
        samples = array.array("h", payload)
        if sys.byteorder != "little":
            samples.byteswap()
        pcm.append(samples)
    assert pcm[0].tobytes() == original_pcm
    assert len(pcm[0]) == len(pcm[1])
    assert all(left * 4 == right for left, right in zip(pcm[0], pcm[1]))
    truth = scorer.mixed_tokens(original_case["reference"])
    old = scorer.mixed_tokens(before["response"]["text"])
    new = scorer.mixed_tokens(after["response"]["text"])
    comparisons.append({
        "id": source_id, "role": "retained_empty_case" if source_id == plan["fixed_problem_id"] else "metadata_selected_quiet_control",
        "reference": original_case["reference"], "language": original_case["language"],
        "original_rms_dbfs": original_case["original_rms_dbfs"],
        "original": {k: before["response"][k] for k in ["text", "language"]},
        "gain4": {k: after["response"][k] for k in ["text", "language"]},
        "reference_tokens": len(truth), "original_edits": scorer.distance(truth, old),
        "gain4_edits": scorer.distance(truth, new), "normalized_equal": old == new,
        "original_empty": not old, "gain4_empty": not new,
    })
sources = json.loads((ROOT / "native-http/source-before.json").read_text())
assert all(sha(PROJECT / relative) == digest for relative, digest in sources.items())
metadata = json.loads((ROOT.parent / "natural-reference-20261107-complete/protected-metadata-before.private.json").read_text())
assert all([Path(path).stat().st_size, Path(path).stat().st_mtime_ns] == expected for path, expected in metadata.items())
try:
    os.kill(runner["owned_helper_pid"], 0)
except ProcessLookupError:
    pass
else:
    raise AssertionError("Owned level Helper remains alive")
with socket.socket() as s:
    assert s.connect_ex(("127.0.0.1", plan["execution"]["port"])) != 0
assert not list((ROOT / "native-http/service/tmp").glob("request-*"))
production = Path.home() / "Library/Application Support/CyberAudio"
with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
    state = json.load(response)
assert {"instance": state["instance"], "ready": state["status"] == "ready", "config_sha256": sha(production / "config/service.json"), "token_sha256": sha(production / "config/api-token")} == runner["production_before"]
report = {
    "status": "level_pilot_verified_not_candidate_accepted", "verified_at": datetime.now(timezone.utc).isoformat(),
    "protocol_sha256": sha(ROOT / "protocol.json"), "analyzer_sha256": sha(__file__),
    "requests": 14, "original_baseline_parity": 7, "exact_gain_transforms_verified": 7,
    "ffmpeg_exact_transforms_verified": len(json.loads((ROOT / "transform-verification.json").read_text())["cases"]),
    "fewer_edits": sum(row["gain4_edits"] < row["original_edits"] for row in comparisons),
    "more_edits": sum(row["gain4_edits"] > row["original_edits"] for row in comparisons),
    "equal_edit_count": sum(row["gain4_edits"] == row["original_edits"] for row in comparisons),
    "original_edits": sum(row["original_edits"] for row in comparisons), "gain4_edits": sum(row["gain4_edits"] for row in comparisons),
    "source_count": len(sources), "protected_metadata_count": len(metadata),
    "production_preserved": True, "source_preserved": True, "protected_metadata_preserved": True,
    "owned_helper_absent": True, "owned_listener_closed": True, "temporary_request_count": 0,
    "cases": comparisons, "candidate_promoted": False, "original_gates_preserved": True,
    "limits": plan["limits"], "data_license": plan["data_license"], "data_attribution": plan["data_attribution"],
    "evidence_sha256": {name: sha(ROOT / name) for name in ["protocol.json", "native-http/runner.json", "native-http/results.jsonl", "transform-verification.json"]},
}
assert not (ROOT / "summary.private.json").exists()
(ROOT / "summary.private.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({k: v for k, v in report.items() if k not in ["evidence_sha256", "limits"]}, ensure_ascii=False, indent=2))
