"""Verify all fixed generations, complete-ASR routes and preservation from files."""
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import urllib.request
import wave

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def distance(left, right):
    table = [[0] * (len(right) + 1) for _ in range(len(left) + 1)]
    for i in range(len(left) + 1): table[i][0] = i
    for j in range(len(right) + 1): table[0][j] = j
    for i, a in enumerate(left, 1):
        for j, b in enumerate(right, 1):
            table[i][j] = min(table[i-1][j]+1, table[i][j-1]+1, table[i-1][j-1]+(a != b))
    return table[-1][-1]


plan = json.loads((ROOT / "protocol.json").read_text())
generation_runner = json.loads((ROOT / "runner.json").read_text())
asr_runner = json.loads((ROOT / "roundtrip-http/runner.json").read_text())
build = json.loads((ROOT / "build/build.json").read_text())
assert generation_runner["status"] == "generations_complete_not_content_accepted"
assert generation_runner["necessary_generation_gate_passed"]
assert generation_runner["protocol_sha256"] == sha(ROOT / "protocol.json")
assert plan["files_sha256"] == {name: sha(ROOT / name) for name in plan["files_sha256"]}
assert build["status"] == "built_no_model" and build["shared_inputs_preserved"]
assert build["protected_inputs_sha256"] == {path: sha(path) for path in build["protected_inputs_sha256"]}
assert sha(plan["sdk_source"]) == plan["sdk_source_sha256"]
insertion = (ROOT / "insertion.swift.txt").read_text()
assert (ROOT / "build/Qwen3TTS.swift").read_text().replace(insertion, "", 1) == Path(plan["sdk_source"]).read_text()
assert sha(plan["baseline_reference"]) == plan["baseline_reference_sha256"]
reference = json.loads(Path(plan["baseline_reference"]).read_text())["results"]
eos = json.loads((Path(plan["model_directory"]) / "config.json").read_text())["talker_config"]["codec_eos_token_id"]
arms = {}
pcm_by_arm = {}
for arm in ["baseline", "candidate"]:
    report = json.loads((ROOT / arm / "report.json").read_text())
    assert report["status"] == "diagnostic_completed" and report["plan_sha256"] == sha(ROOT / "protocol.json")
    assert [row["case"] for row in report["results"]] == plan["cases"]
    assert sha(ROOT / "build" / (arm + "-probe")) == build["executables_sha256"][arm]
    arms[arm] = report["results"]
    pcm_by_arm[arm] = {}
    for row in report["results"]:
        pcm = (ROOT / arm / row["pcm_file"]).read_bytes()
        assert hashlib.sha256(pcm).hexdigest() == row["pcm_sha256"]
        assert row["token_count"] == len(row["tokens"])
        assert row["eos_seen"] == (row["tokens"][-1] == eos)
        assert len(pcm) == (len(row["tokens"]) - int(row["eos_seen"])) * 3840
        assert row["pcm_bytes"] == len(pcm) and row["audio_seconds"] == len(pcm) / 48000
        pcm_by_arm[arm][row["id"]] = pcm
assert all(left["tokens"] == right["tokens"] and left["pcm_sha256"] == right["pcm_sha256"] for left, right in zip(arms["baseline"], reference))
comparisons = []
for old, new in zip(arms["baseline"], arms["candidate"]):
    assert old["id"] == new["id"] and old["case"] == new["case"]
    events = [i for i in range(8, len(new["tokens"])) if len(set(new["tokens"][i-8:i])) == 1 and new["tokens"][i-1] != eos]
    is_failure = old["id"].startswith("known-failure")
    if is_failure:
        assert old["tokens"][:47] == new["tokens"][:47] and old["tokens"][47] != new["tokens"][47]
        assert pcm_by_arm["baseline"][old["id"]][:168960] == pcm_by_arm["candidate"][new["id"]][:168960]
        assert new["eos_seen"] and events[0] == 47
    else:
        assert not events and old["tokens"] == new["tokens"] and old["pcm_sha256"] == new["pcm_sha256"]
    comparisons.append({"id": old["id"], "baseline_eos": old["eos_seen"], "candidate_eos": new["eos_seen"],
                        "baseline_tokens": len(old["tokens"]), "candidate_tokens": len(new["tokens"]),
                        "baseline_seconds": old["audio_seconds"], "candidate_seconds": new["audio_seconds"],
                        "full_tokens_equal": old["tokens"] == new["tokens"], "full_pcm_equal": old["pcm_sha256"] == new["pcm_sha256"],
                        "derived_candidate_condition_count": len(events), "first_derived_condition_index": events[0] if events else None})
failures = [row for row in arms["candidate"] if row["id"].startswith("known-failure")]
assert failures[0]["tokens"] == failures[1]["tokens"] and failures[0]["pcm_sha256"] == failures[1]["pcm_sha256"]
manifest = json.loads((ROOT / "roundtrip-manifest.json").read_text())
assert manifest["protocol_sha256"] == sha(ROOT / "protocol.json")
requests = list(map(json.loads, (ROOT / "roundtrip-http/results.jsonl").read_text().splitlines()))
assert [row["id"] for row in requests] == [row["id"] for row in manifest["cases"]]
assert asr_runner["status"] == "whole_test_transcribed_not_quality_accepted" and asr_runner["completed_requests"] == manifest["expected_requests"] == len(requests) == 36
assert asr_runner["owned_helper_exit_code"] == 0
assert asr_runner["study_files_sha256"] == {name: sha(ROOT / name) for name in asr_runner["study_files_sha256"]}
pieces = defaultdict(list)
for case, request in zip(manifest["cases"], requests):
    assert request["http_status"] == 200 and sha(case["audio"]) == case["audio_sha256"]
    assert abs(request["response"]["duration"] - case["frames"]/16000) < 1e-8
    assert case["request_language"] == ("auto" if case["route"] == "auto" else {"chinese":"zh","english":"en"}[next(row for row in plan["cases"] if row["id"] == case["source_id"])["language"]])
    with wave.open(case["audio"], "rb") as sound:
        assert (sound.getnchannels(), sound.getsampwidth(), sound.getframerate()) == (1, 2, 24000)
        payload = sound.readframes(sound.getnframes())
    start = case["part"] * 1152000
    assert payload == pcm_by_arm[case["arm"]][case["source_id"]][start:start+1152000]
    pieces[(case["arm"], case["source_id"], case["route"])].append((case["part"], case["parts"], request["response"]["text"]))
spec = importlib.util.spec_from_file_location("frozen_repeated_code_scorer", ROOT.parent / "post-delivery-20261107/score-natural-asr.py")
scorer = importlib.util.module_from_spec(spec); spec.loader.exec_module(scorer); scorer.self_test()
texts = {}
for key, parts in pieces.items():
    parts.sort(); assert [item[0] for item in parts] == list(range(parts[0][1]))
    text = " ".join(item[2] for item in parts)
    case = next(row for row in plan["cases"] if row["id"] == key[1])
    expected, actual = scorer.mixed_tokens(case["text"]), scorer.mixed_tokens(text)
    edits = distance(expected, actual); assert edits == scorer.distance(expected, actual)
    texts[key] = {"arm": key[0], "id": key[1], "route": key[2], "text": text, "reference": case["text"], "reference_tokens": len(expected), "edits": edits, "normalized_exact": expected == actual}
known_content_passed = all(texts[("candidate", row["id"], route)]["normalized_exact"] for row in failures for route in ["auto", "producer"])
control_content_passed = all(scorer.mixed_tokens(texts[("baseline", row["id"], route)]["text"]) == scorer.mixed_tokens(texts[("candidate", row["id"], route)]["text"]) for row in plan["cases"] if not row["id"].startswith("known-failure") for route in ["auto", "producer"])
assert known_content_passed and control_content_passed
sources = json.loads((ROOT / "source-before.json").read_text())
assert all(sha(PROJECT / relative) == digest for relative, digest in sources.items())
metadata = json.loads((ROOT.parent / "natural-reference-20261107-complete/protected-metadata-before.private.json").read_text())
assert all([Path(path).stat().st_size, Path(path).stat().st_mtime_ns] == value for path, value in metadata.items())
app_build = json.loads((Path(plan["app"]).parent / "build.json").read_text())
assert all(sha(PROJECT / relative) == digest for relative, digest in app_build["frozen_sha256"].items())
assert all(sha(Path(plan["app"]) / relative) == value["sha256"] for relative, value in app_build["files"].items())
assert all(generation_runner[key] and asr_runner[key] for key in ["production_preserved", "source_preserved"])
with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response: state = json.load(response)
assert state["instance"] == generation_runner["production_before"]["instance"] == asr_runner["production_before"]["instance"]
for name, key in [("service.json","config_sha256"),("api-token","token_sha256")]:
    assert sha(Path.home()/"Library/Application Support/CyberAudio/config"/name) == generation_runner["production_before"][key] == asr_runner["production_before"][key]
processes = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True).splitlines()
assert not any(str(ROOT / "build" / (arm + "-probe")) + " " in line for arm in ["baseline", "candidate"] for line in processes)
assert not any(str(ROOT / "roundtrip-http/service") in line for line in processes)
with socket.socket() as listener: assert listener.connect_ex(("127.0.0.1", plan["execution"]["port"])) != 0
assert not list((ROOT / "roundtrip-http/service/tmp").glob("request-*"))
summary = {"status": "fixed_repeated_code_pilot_verified", "verified_at": datetime.now(timezone.utc).isoformat(),
           "protocol_sha256": sha(ROOT / "protocol.json"), "generations": 12, "fresh_baseline_byte_matches": 6,
           "candidate_failure_eos_repeats": 2, "candidate_failure_tokens": 113, "candidate_failure_seconds": 8.96,
           "failure_prefix47_tokens_and44_frames_exact": True, "normal_controls_full_bytes_exact": 4,
           "candidate_repeats_exact": True, "asr_requests": 36, "combined_asr_routes": 24,
           "known_candidate_routes_exact": 4, "all_eight_control_routes_preserved": True,
           "pilot_qualified_for_independent_full_cohort": True, "candidate_promoted": False,
           "source_files_verified": len(sources), "protected_metadata_verified": len(metadata),
           "shared_build_inputs_verified": len(build["protected_inputs_sha256"]), "frozen_artifacts_verified": len(app_build["frozen_sha256"]),
           "current_app_files_verified": len(app_build["files"]), "production_preserved": True,
           "owned_model_processes_absent": True, "owned_listener_closed": True, "temporary_requests": 0,
           "comparisons": comparisons, "roundtrips": list(texts.values()),
           "limits": ["Derived codec recurrence is not an internal attention trace or audible-loop attribution", "No full cohort, original strict M4 or human intelligibility acceptance from this pilot", "Original natural-ASR/public-download/physical/distribution gates remain open"]}
with (ROOT / "summary.private.json").open("x") as stream: json.dump(summary, stream, ensure_ascii=False, indent=2); stream.write("\n")
print(json.dumps({key: value for key, value in summary.items() if key not in ["comparisons", "roundtrips", "limits"]}, indent=2))
