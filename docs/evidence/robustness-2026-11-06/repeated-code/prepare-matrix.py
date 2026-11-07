"""Freeze the original 144-case cohort and current-route retained baselines."""
from pathlib import Path
from datetime import datetime, timezone
from itertools import product
import ast
import hashlib
import json

ROOT = Path(__file__).resolve().parent
OLD = ROOT.parent / "sampling-parity-20261107"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


assert not (ROOT / "matrix-plan.json").exists()
pilot = json.loads((ROOT / "summary.private.json").read_text())
assert pilot["pilot_qualified_for_independent_full_cohort"]
protocol = json.loads((ROOT / "protocol.json").read_text())
old_plan = json.loads((OLD / "matrix-plan.json").read_text())
cases = old_plan["cases"]
assert len(cases) == len({row["id"] for row in cases}) == 144
assert {(row["text_id"], row["voice"], row["seed"]) for row in cases} == set(product(
    {row["text_id"] for row in cases}, {row["voice"] for row in cases}, {row["seed"] for row in cases}))
assert [len({row[key] for row in cases}) for key in ["text_id", "voice", "seed"]] == [4, 9, 4]
previous = [row for row in json.loads((OLD / "matrix-cases.json").read_text()) if row["source_group"] == "baseline"]
by_id = {row["id"].removeprefix("baseline-"): row for row in previous}
assert len(previous) == len(by_id) == 144
eos = json.loads((Path(protocol["model_directory"]) / "config.json").read_text())["talker_config"]["codec_eos_token_id"]
baselines = []
for case in cases:
    old = by_id[case["id"]]
    assert all(old[key] == case[key] for key in ["text_id", "text", "language", "voice", "seed"])
    pcm = Path(old["pcm_source"]) if old["reused"] else OLD / "matrix-baseline" / (case["id"] + ".pcm")
    tokens_path = Path(old["tokens_source"]) if old["reused"] else OLD / "matrix-baseline" / (case["id"] + "-tokens.json")
    tokens = json.loads(tokens_path.read_text())
    assert sha(pcm) == old["pcm_sha256"] and sha(tokens_path) == old["tokens_sha256"]
    assert len(tokens) == old["token_count"] and (tokens[-1] == eos) == old["eos_seen"]
    assert pcm.stat().st_size == (len(tokens) - int(old["eos_seen"])) * 3840
    baselines.append({"id": case["id"], "case": case, "pcm_source": str(pcm.resolve()),
                      "tokens_source": str(tokens_path.resolve()), "pcm_sha256": sha(pcm),
                      "tokens_sha256": sha(tokens_path), "token_count": len(tokens),
                      "audio_seconds": pcm.stat().st_size / 48000, "eos_seen": old["eos_seen"],
                      "origin": "original-retained" if old["reused"] else "current-explicit-English-route"})
swift = (OLD / "OrderMatrixProbe.swift").read_text().replace("OrderMatrixProbe", "RepeatedCodeMatrixProbe")
swift = swift.replace('["baseline", "candidate"].contains(args[6])', 'args[6] == "candidate"')
(ROOT / "RepeatedCodeMatrixProbe.swift").write_text(swift)
for name in ["prepare-matrix.py", "build-matrix.py", "run-matrix.py", "prepare-matrix-roundtrip.py"]:
    ast.parse((ROOT / name).read_text())
plan = {"purpose": "Independent complete original cohort for the private repeated-main-code exclusion; no production promotion",
        "created_at": datetime.now(timezone.utc).isoformat(), "source_commit": protocol["source_commit"],
        "pilot_protocol_sha256": sha(ROOT / "protocol.json"), "pilot_summary_sha256": sha(ROOT / "summary.private.json"),
        "source_cohort_plan_sha256": sha(OLD / "matrix-plan.json"), "source_cohort_results_sha256": sha(OLD / "matrix-cases.json"),
        "cases": cases, "baselines": baselines, "fresh_baseline_ids": [], "expected_fresh_baselines": 0,
        "expected_candidates": 144, "preliminary_candidate_report_sha256": sha(ROOT / "candidate/report.json"),
        "temperature": 0.7, "subtalker_temperature": 0.7, "top_k": 50, "top_p": 1.0, "repetition_penalty": 1.05,
        "max_tokens": 1200, "individual_budget_seconds": 120, "per_stage_budget_seconds": 2000,
        "maximum_disk_bytes": 1024 ** 3, "app": protocol["app"], "model_directory": protocol["model_directory"],
        "metallib": protocol["metallib"], "execution": dict(protocol["execution"], port=18904, whole_timeout_seconds=1800),
        "selection": "The unchanged original 4 texts x 9 voices x 4 seeds in original order. Seed196 was selected for an earlier failure; this is a diagnostic cohort, not a population estimate.",
        "baseline_policy": "Reuse all144 current-route baseline bytes:128 original +16 explicit-English eric/dylan. Pilot six fresh original runs reproduced all retained tokens/PCM before this matrix. Baseline ASR is fresh in both routes.",
        "rules": ["Link only the pilot private TTS objects to a new managed-entry caller; preserve the single original insertion and all shared inputs.",
                  "First candidate must exactly reproduce the pilot failure's complete tokens/PCM; thereafter complete all144 once regardless of EOS outcome. No new seed, text, threshold, restart, retry, truncation or forced EOS.",
                  "After generation exits, transcribe all288 baseline/candidate complete outputs in <=24s deterministic chunks with both auto and original producer language. Retain all pieces and concatenate in order; no retries or omissions.",
                  "Use the unchanged author mixed-token scorer. Qualify only if all144 candidates have actual EOS, none is over15s, and no paired candidate edit count is higher in either route. Require the known failure to be exact in both routes and all untriggered paths to retain complete original token/PCM bytes.",
                  "Any failed condition rejects this candidate without parameter tuning. Passing this cohort only qualifies further independent target/listening/service validation; original strict M4, natural ASR, complete public download, physical and distribution gates remain open."],
        "files_sha256": {name: sha(ROOT / name) for name in ["RepeatedCodeMatrixProbe.swift", "prepare-matrix.py", "build-matrix.py", "run-matrix.py", "prepare-matrix-roundtrip.py"]}}
with (ROOT / "matrix-plan.json").open("x") as stream:
    json.dump(plan, stream, ensure_ascii=False, indent=2); stream.write("\n")
print(json.dumps({"status": "matrix_frozen_no_inference", "cases": 144, "retained_current_route_baselines": 144,
                  "protocol_sha256": sha(ROOT / "matrix-plan.json"), "scoring": "original mixed tokens; both ASR routes"}))
