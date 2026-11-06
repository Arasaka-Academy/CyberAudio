"""Paired descriptive diagnostics; never interpret ASR roundtrip as listening."""
from collections import defaultdict
from pathlib import Path
import hashlib
import json
import statistics
import unicodedata

base = Path(__file__).resolve().parent
matrix_path = base / "matrix-candidate/report.json"
asr_path = base / "matrix-roundtrip/reports/asr-followup.json"
matrix = json.loads(matrix_path.read_text())
asr = json.loads(asr_path.read_text())
assert matrix["status"] == asr["status"] == "diagnostic_completed"
assert asr["owned_helper_exit_code"] == 0 and asr["production_and_config_preserved"]
assert matrix["completed_generations"] == 144 and len(asr["results"]) == asr["expected_requests"]


def normalized(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def distance(left, right):
    previous = list(range(len(right) + 1))
    for index, a in enumerate(left, 1):
        row = [index]
        for column, b in enumerate(right, 1):
            row.append(min(previous[column] + 1, row[-1] + 1, previous[column - 1] + (a != b)))
        previous = row
    return previous[-1]


baseline_path = base / "matrix-baseline/report.json"
baseline = json.loads(baseline_path.read_text())
plan = json.loads((base / "matrix-plan.json").read_text())
retained = plan["reused_baselines"]
assert len(retained) == 128 and len(baseline["results"]) == 16
old = [dict(row, id="baseline-" + row["id"].removesuffix("-t7"), source_group="baseline") for row in retained]
fresh = [dict(row, id="baseline-" + row["id"], source_group="baseline") for row in baseline["results"]]
new = [dict(row, id="candidate-" + row["id"], source_group="candidate") for row in matrix["results"]]
matrix["results"] = old+fresh+new
assert len(matrix["results"]) == 288
parts = defaultdict(list)
for row in asr["results"]:
    assert row["http_status"] == 200
    parts[row["source_id"]].append(row)
cases = []
for source in matrix["results"]:
    rows = sorted(parts[source["id"]], key=lambda x: x["part"])
    assert rows and [r["part"] for r in rows] == list(range(len(rows)))
    text = " ".join(row["response"]["text"] for row in rows)
    reference = normalized(source["text"])
    observed = normalized(text)
    cases.append(source | {"roundtrip_text": text, "segments": len(rows),
                          "roundtrip_normalized_exact": reference == observed,
                          "roundtrip_alphanumeric_edits": distance(reference, observed)})
by_pair = defaultdict(dict)
for row in cases:
    by_pair[(row["text_id"], row["voice"], row["seed"])][row["source_group"]] = row
pairs = []
for (text_id, voice, seed), values in by_pair.items():
    assert set(values) == {"baseline", "candidate"}
    before, after = values["baseline"], values["candidate"]
    delta = after["roundtrip_alphanumeric_edits"] - before["roundtrip_alphanumeric_edits"]
    pairs.append({"text_id": text_id, "voice": voice, "seed": seed, "reference": before["text"],
                  "baseline": {k: before[k] for k in ["id", "eos_seen", "audio_seconds", "roundtrip_text", "roundtrip_alphanumeric_edits"]},
                  "candidate": {k: after[k] for k in ["id", "eos_seen", "audio_seconds", "roundtrip_text", "roundtrip_alphanumeric_edits"]},
                  "candidate_edit_delta": delta})
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
summary = {"status": "diagnostic_completed", "source_sha256": {"matrix": sha(matrix_path), "baseline_matrix": sha(baseline_path), "asr": sha(asr_path)},
           "generations": len(cases), "pairs": len(pairs), "asr_requests": len(asr["results"]),
           "scope": "Fixed four texts / nine voices / four predeclared seeds; seed 196 was selected from earlier failure. Not a population failure rate.",
           "scoring_limit": "Alphanumeric edit distance after NFKC/casefold, punctuation/space removal. No number normalization. Combined TTS/ASR errors, dialect and number notation can affect this diagnostic; no human listening evidence.",
           "default_parameters_changed": False, "candidate_promoted": False, "groups": []}
for group in ["baseline", "candidate"]:
    rows = [r for r in cases if r["source_group"] == group]
    summary["groups"].append({"group": group, "temperature": 0.7, "temperature_before_top_k": group == "candidate", "generations": len(rows),
        "without_eos": sum(not r["eos_seen"] for r in rows),
        "over_15_audio_seconds": sum(r["audio_seconds"] > 15 for r in rows),
        "median_audio_seconds": statistics.median(r["audio_seconds"] for r in rows),
        "maximum_audio_seconds": max(r["audio_seconds"] for r in rows),
        "roundtrip_normalized_exact": sum(r["roundtrip_normalized_exact"] for r in rows),
        "roundtrip_total_alphanumeric_edits": sum(r["roundtrip_alphanumeric_edits"] for r in rows)})
summary["paired_roundtrip_edits"] = {"candidate_fewer": sum(p["candidate_edit_delta"] < 0 for p in pairs),
    "equal": sum(p["candidate_edit_delta"] == 0 for p in pairs), "candidate_more": sum(p["candidate_edit_delta"] > 0 for p in pairs)}
summary["largest_candidate_increases"] = sorted(pairs, key=lambda p: p["candidate_edit_delta"], reverse=True)[:10]
summary["known_failure_pair"] = next(p for p in pairs if p["text_id"] == "zh-stability" and p["voice"] == "ono_anna" and p["seed"] == 196)
summary["original_baselines_reused"] = 128
summary["current_route_baselines_generated"] = 16
summary["candidate_generations"] = 144
summary["baseline_language_routing_current"] = True
summary["plan_sha256"] = sha(base / "matrix-plan.json")
path = base / "matrix-summary.json"
assert not path.exists()
path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
(base / "matrix-paired-results.json").write_text(json.dumps(pairs, ensure_ascii=False, indent=2) + "\n")
(base / "matrix-cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(summary, ensure_ascii=False, indent=2))
