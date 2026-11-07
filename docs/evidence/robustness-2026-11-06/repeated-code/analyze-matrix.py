"""Verify the entire frozen cohort, both complete ASR routes, and preservation."""
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import socket
import statistics
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
    for i in range(len(left) + 1):
        table[i][0] = i
    for j in range(len(right) + 1):
        table[0][j] = j
    for i, a in enumerate(left, 1):
        for j, b in enumerate(right, 1):
            table[i][j] = min(table[i-1][j]+1, table[i][j-1]+1, table[i-1][j-1]+(a != b))
    return table[-1][-1]


plan = json.loads((ROOT / "matrix-plan.json").read_text())
analysis_plan = json.loads((ROOT / "matrix-analysis-protocol.json").read_text())
assert analysis_plan["matrix_plan_sha256"] == sha(ROOT / "matrix-plan.json")
assert analysis_plan["analyzer_sha256"] == sha(__file__)
assert plan["files_sha256"] == {name: sha(ROOT / name) for name in plan["files_sha256"]}
scorer_path = ROOT.parent / "post-delivery-20261107/score-natural-asr.py"
assert sha(scorer_path) == analysis_plan["scorer_sha256"]
spec = importlib.util.spec_from_file_location("frozen_repeated_matrix_scorer", scorer_path)
scorer = importlib.util.module_from_spec(spec); spec.loader.exec_module(scorer); scorer.self_test()
pilot_plan = json.loads((ROOT / "protocol.json").read_text())
pilot = json.loads((ROOT / "summary.private.json").read_text())
assert sha(ROOT / "protocol.json") == plan["pilot_protocol_sha256"]
assert sha(ROOT / "summary.private.json") == plan["pilot_summary_sha256"]
assert pilot["pilot_qualified_for_independent_full_cohort"]
sdk = Path(pilot_plan["sdk_source"])
assert sha(sdk) == pilot_plan["sdk_source_sha256"]
insertion = (ROOT / "insertion.swift.txt").read_text()
assert sha(ROOT / "insertion.swift.txt") == pilot_plan["insertion_sha256"]
assert (ROOT / "build/Qwen3TTS.swift").read_text().replace(insertion, "", 1) == sdk.read_text()
build = json.loads((ROOT / "matrix-build/build.json").read_text())
pilot_build = json.loads((ROOT / "build/build.json").read_text())
assert build["status"] == "built_no_inference" and build["shared_inputs_preserved"] and build["private_objects_preserved"]
assert sha(ROOT / "matrix-build/candidate-matrix-probe") == build["executable_sha256"]
assert build["private_objects_sha256"] == {name: sha(name) for name in build["private_objects_sha256"]}
assert pilot_build["protected_inputs_sha256"] == {name: sha(name) for name in pilot_build["protected_inputs_sha256"]}
run = json.loads((ROOT / "matrix-runner.json").read_text())
assert run["status"] == "all144_generated_not_content_accepted" and run["owned_model_exit_code"] == 0
assert run["plan_sha256"] == sha(ROOT / "matrix-plan.json")
assert run["candidate_report_sha256"] == sha(ROOT / "matrix-candidate/report.json")
generated = json.loads((ROOT / "matrix-candidate/report.json").read_text())
assert generated["status"] == "diagnostic_completed" and generated["completed_generations"] == len(generated["results"]) == 144
assert generated["preliminary_candidate_parity"]
assert [row["id"] for row in generated["results"]] == [row["id"] for row in plan["cases"]]
eos = json.loads((Path(plan["model_directory"]) / "config.json").read_text())["talker_config"]["codec_eos_token_id"]
by_id, pcm = {}, {}
comparisons = []
for original, candidate, case in zip(plan["baselines"], generated["results"], plan["cases"]):
    assert original["case"] == case and all(candidate[key] == case[key] for key in case)
    old_tokens = json.loads(Path(original["tokens_source"]).read_text())
    new_token_file = ROOT / "matrix-candidate" / (case["id"] + "-tokens.json")
    new_tokens = json.loads(new_token_file.read_text())
    old_pcm = Path(original["pcm_source"]).read_bytes()
    new_pcm = (ROOT / "matrix-candidate" / (case["id"] + ".pcm")).read_bytes()
    assert sha(original["tokens_source"]) == original["tokens_sha256"]
    assert sha(new_token_file) == candidate["tokens_sha256"]
    for arm, row, tokens, payload in [("baseline", original, old_tokens, old_pcm), ("candidate", candidate, new_tokens, new_pcm)]:
        assert len(tokens) == row["token_count"] and (tokens[-1] == eos) == row["eos_seen"]
        assert len(payload) == (len(tokens)-int(row["eos_seen"])) * 3840
        assert hashlib.sha256(payload).hexdigest() == row["pcm_sha256"] and len(payload)/48000 == row["audio_seconds"]
        by_id[(arm, case["id"])] = row
        pcm[(arm, case["id"])] = payload
    old_events = [i for i in range(8, len(old_tokens)) if old_tokens[i-1] != eos and len(set(old_tokens[i-8:i])) == 1]
    new_events = [i for i in range(8, len(new_tokens)) if new_tokens[i-1] != eos and len(set(new_tokens[i-8:i])) == 1]
    full_equal = old_tokens == new_tokens and old_pcm == new_pcm
    if not old_events:
        assert full_equal and not new_events, "Untriggered path changed: " + case["id"]
    else:
        first = old_events[0]
        assert old_tokens[:first] == new_tokens[:first] and old_tokens[first] != new_tokens[first]
        assert old_pcm[:first//4*15360] == new_pcm[:first//4*15360]
        assert new_events and new_events[0] == first
    comparisons.append({"id": case["id"], "baseline_eos": original["eos_seen"], "candidate_eos": candidate["eos_seen"],
                        "baseline_seconds": original["audio_seconds"], "candidate_seconds": candidate["audio_seconds"],
                        "baseline_tokens": len(old_tokens), "candidate_tokens": len(new_tokens),
                        "baseline_condition_count": len(old_events), "candidate_condition_count": len(new_events),
                        "first_condition_index": old_events[0] if old_events else None, "full_tokens_and_pcm_exact": full_equal})
manifest = json.loads((ROOT / "matrix-roundtrip-manifest.json").read_text())
assert manifest["protocol_sha256"] == sha(ROOT / "matrix-plan.json") and manifest["outputs"] == 288
asr = json.loads((ROOT / "matrix-roundtrip-http/runner.json").read_text())
assert asr["status"] == "whole_test_transcribed_not_quality_accepted" and asr["owned_helper_exit_code"] == 0
assert asr["study_protocol_sha256"] == sha(ROOT / "matrix-plan.json")
assert asr["study_files_sha256"] == {name:sha(ROOT/name) for name in asr["study_files_sha256"]}
requests = list(map(json.loads, (ROOT / "matrix-roundtrip-http/results.jsonl").read_text().splitlines()))
assert len(requests) == asr["completed_requests"] == manifest["expected_requests"]
assert [row["id"] for row in requests] == [row["id"] for row in manifest["cases"]]
assert len({row["id"] for row in requests}) == len(requests)
case_lookup = {row["id"]:row for row in plan["cases"]}
parts = defaultdict(list)
for row, request in zip(manifest["cases"], requests):
    assert request["http_status"] == 200 and sha(row["audio"]) == row["audio_sha256"]
    case = case_lookup[row["source_id"]]
    assert row["reference"] == case["text"]
    assert row["request_language"] == ("auto" if row["route"] == "auto" else {"chinese":"zh", "english":"en"}[case["language"]])
    with wave.open(row["audio"], "rb") as sound:
        assert (sound.getnchannels(), sound.getsampwidth(), sound.getframerate()) == (1, 2, 24000)
        payload = sound.readframes(sound.getnframes())
    start = row["part"] * 1152000
    assert payload == pcm[(row["arm"], row["source_id"])][start:start+1152000]
    assert len(payload) <= 1152000 and row["frames"] == len(payload)//3
    assert abs(request["response"]["duration"] - row["frames"]/16000) < 1e-8
    parts[(row["arm"], row["source_id"], row["route"])].append((row["part"], row["parts"], request["response"]["text"]))
assert len(parts) == 576
texts = {}
for key, rows in parts.items():
    rows.sort()
    assert [row[0] for row in rows] == list(range(rows[0][1]))
    observed = " ".join(row[2] for row in rows)
    expected = scorer.mixed_tokens(case_lookup[key[1]]["text"])
    actual = scorer.mixed_tokens(observed)
    edits = distance(expected, actual)
    assert edits == scorer.distance(expected, actual)
    texts[key] = {"arm":key[0], "id":key[1], "route":key[2], "text":observed, "reference":case_lookup[key[1]]["text"],
                  "edits":edits, "reference_tokens":len(expected), "normalized_exact":expected == actual}
pairs = []
for case in plan["cases"]:
    for route in ["auto", "producer"]:
        left, right = texts[("baseline", case["id"], route)], texts[("candidate", case["id"], route)]
        pairs.append({"id":case["id"], "route":route, "baseline":left, "candidate":right, "edit_delta":right["edits"]-left["edits"]})
route_summaries = []
for route in ["auto", "producer"]:
    rows = [row for row in pairs if row["route"] == route]
    route_summaries.append({"route":route, "pairs":len(rows), "improved":sum(row["edit_delta"] < 0 for row in rows),
                            "equal":sum(row["edit_delta"] == 0 for row in rows), "worse":sum(row["edit_delta"] > 0 for row in rows),
                            "baseline_exact":sum(row["baseline"]["normalized_exact"] for row in rows),
                            "candidate_exact":sum(row["candidate"]["normalized_exact"] for row in rows),
                            "baseline_edits":sum(row["baseline"]["edits"] for row in rows),
                            "candidate_edits":sum(row["candidate"]["edits"] for row in rows)})
known = "zh-stability-ono_anna-s196"
conditions = {"all144_actual_eos":all(row["eos_seen"] for row in generated["results"]),
              "all144_within15_seconds":all(row["audio_seconds"] <= 15 for row in generated["results"]),
              "no_pair_worse_either_route":all(row["edit_delta"] <= 0 for row in pairs),
              "known_failure_exact_both_routes":all(texts[("candidate",known,route)]["normalized_exact"] for route in ["auto","producer"]),
              "all_untriggered_complete_bytes_preserved":all(row["full_tokens_and_pcm_exact"] for row in comparisons if not row["baseline_condition_count"])}
sources = json.loads((ROOT / "matrix-source-before.json").read_text())
assert all(sha(PROJECT/name) == value for name,value in sources.items())
metadata = json.loads((ROOT.parent / "natural-reference-20261107-complete/protected-metadata-before.private.json").read_text())
assert all([Path(name).stat().st_size, Path(name).stat().st_mtime_ns] == value for name,value in metadata.items())
app_build = json.loads((Path(plan["app"]).parent / "build.json").read_text())
assert all(sha(PROJECT/name) == value for name,value in app_build["frozen_sha256"].items())
assert all(sha(Path(plan["app"])/name) == value["sha256"] for name,value in app_build["files"].items())
assert run["production_preserved"] and run["source_preserved"] and asr["production_preserved"] and asr["source_preserved"]
with urllib.request.urlopen("http://127.0.0.1:8765/ready",timeout=5) as response:
    state=json.load(response)
assert state["instance"] == run["production_before"]["instance"] == asr["production_before"]["instance"]
for name,key in [("service.json","config_sha256"),("api-token","token_sha256")]:
    assert sha(Path.home()/"Library/Application Support/CyberAudio/config"/name) == run["production_before"][key] == asr["production_before"][key]
processes = subprocess.check_output(["/bin/ps","-axo","pid=,command="],text=True).splitlines()
assert not any(str(ROOT/"matrix-build/candidate-matrix-probe")+" " in line for line in processes)
assert not any(str(ROOT/"matrix-roundtrip-http/service") in line for line in processes)
with socket.socket() as listener:
    assert listener.connect_ex(("127.0.0.1",plan["execution"]["port"])) != 0
assert not list((ROOT/"matrix-roundtrip-http/service/tmp").glob("request-*"))
summary = {"status":"fixed_repeated_code_full_cohort_verified", "verified_at":datetime.now(timezone.utc).isoformat(),
           "protocol_sha256":sha(ROOT/"matrix-plan.json"), "generations":144,"retained_current_route_baselines":144,
           "complete_outputs_read_back":288,"asr_requests":len(requests),"combined_routes":576,
           "all_requests_http200":True,"no_retries_or_omissions":True,"conditions":conditions,
           "full_cohort_qualified_for_target_validation":all(conditions.values()),"candidate_promoted":False,
           "routes":route_summaries,"no_condition_complete_bytes_exact":sum(not row["baseline_condition_count"] for row in comparisons),
           "condition_paths":[row for row in comparisons if row["baseline_condition_count"]],
           "groups":[{"arm":arm,"without_eos":sum(not by_id[(arm,case["id"])]["eos_seen"] for case in plan["cases"]),
                      "over15_seconds":sum(by_id[(arm,case["id"])]["audio_seconds"] > 15 for case in plan["cases"]),
                      "median_audio_seconds":statistics.median(by_id[(arm,case["id"])]["audio_seconds"] for case in plan["cases"]),
                      "maximum_audio_seconds":max(by_id[(arm,case["id"])]["audio_seconds"] for case in plan["cases"])} for arm in ["baseline","candidate"]],
           "worse_pairs":[row for row in pairs if row["edit_delta"] > 0],"known_failure_pairs":[row for row in pairs if row["id"] == known],
           "source_files_verified":len(sources),"protected_metadata_verified":len(metadata),"shared_build_inputs_verified":len(pilot_build["protected_inputs_sha256"]),
           "frozen_artifacts_verified":len(app_build["frozen_sha256"]),"current_app_files_verified":len(app_build["files"]),
           "production_preserved":True,"owned_model_processes_absent":True,"owned_listener_closed":True,"temporary_requests":0,
           "limits":["Original cohort includes previously selected failure seeds; not a population failure rate", "Both ASR routes combine TTS and ASR errors and do not prove human intelligibility",
                     "No target strict soak, natural-ASR, full public-download, physical or distribution gate is closed by this diagnostic"]}
with (ROOT/"matrix-summary.private.json").open("x") as stream:
    json.dump(summary,stream,ensure_ascii=False,indent=2);stream.write("\n")
with (ROOT/"matrix-pairs.private.json").open("x") as stream:
    json.dump(pairs,stream,ensure_ascii=False,indent=2);stream.write("\n")
print(json.dumps({key:summary[key] for key in ["status","generations","asr_requests","conditions","full_cohort_qualified_for_target_validation","routes","condition_paths"]},ensure_ascii=False,indent=2))
