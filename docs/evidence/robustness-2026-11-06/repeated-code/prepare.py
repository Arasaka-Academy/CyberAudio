"""Freeze a conditional repeated-code exclusion diagnostic before model work."""
from pathlib import Path
from datetime import datetime, timezone
import ast
import hashlib
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
os.chmod(ROOT, 0o700)
assert not (ROOT / "protocol.json").exists()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


original_plan = PROJECT / "docs/evidence/robustness-2026-11-06/sampling-order/order-plan.json"
old = json.loads(original_plan.read_text())
reference = ROOT.parent / "power-kernel-20261107/generation-original-jit/report.json"
prior = json.loads(reference.read_text())
assert [row["case"] for row in prior["results"]] == old["cases"]
for row in prior["results"]:
    assert sha(reference.parent / row["pcm_file"]) == row["pcm_sha256"]
    events = [i for i in range(8, len(row["tokens"])) if len(set(row["tokens"][i-8:i])) == 1]
    assert (bool(events) and events[0] == 47) if row["id"].startswith("known-failure") else not events
probe = (PROJECT / "docs/evidence/robustness-2026-11-06/sampling-order/OrderCandidateProbe.swift").read_text()
probe = probe.replace("OrderCandidateProbe", "RepeatedCodeProbe").replace('print("CANDIDATE",', 'print("GENERATION",')
(ROOT / "RepeatedCodeProbe.swift").write_text(probe)
description = json.loads((PROJECT / ".context/icon-native-build/arm64-apple-macosx/release/description.json").read_text())
command = description["swiftCommands"]["C.MLXAudioTTS-arm64-apple-macosx-release.module"]
sdk_source = next(Path(p) for p in command["sources"] if p.endswith("/Qwen3TTS.swift"))
insertion = '''        // Private causal diagnostic: a main-code self-loop cannot choose that
        // same code on its next sample. EOS and every other logit are untouched.
        if let eosTokenId, let tokens = generatedTokens, tokens.count >= 8,
           let last = tokens.last, last != eosTokenId,
           tokens.suffix(8).allSatisfy({ $0 == last }) {
            let repeatedId = MLXArray([Int32(last)]).reshaped(1, 1)
            let negativeInfinity = MLXArray.full([1, 1], values: MLXArray(-Float.infinity), dtype: logitsSlice.dtype)
            logitsSlice = putAlong(logitsSlice, repeatedId, values: negativeInfinity, axis: -1)
        }

'''
source = sdk_source.read_text()
assert source.count("        // Greedy if temperature 0\n") == 1
(ROOT / "insertion.swift.txt").write_text(insertion)
model = ROOT.parent / "linked-models/tts"
app = ROOT.parent / "readiness-ownership-20261107/final-bytes-readiness/CyberAudio.app"
build = json.loads((app.parent / "build.json").read_text())
plan = {"purpose": "Private conditional main-code self-loop exclusion; unchanged original sampler order and parameters",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": subprocess.check_output(["rtk", "proxy", "git", "rev-parse", "HEAD"], text=True).strip(),
        "hypothesis": "The retained failure's dominant repeated main code may sustain an absorbing path. Excluding only that code for the next main sample after eight identical consecutive IDs tests escape, without forcing EOS or clipping audio.",
        "basis": {"prior_report_sha256": sha(reference), "failure_tokens": 1200, "largest_id_share": 0.8158333333333333,
                  "longest_identical_run": 119, "first_condition_index": 47, "four_normal_controls_trigger_count": 0,
                  "limits": "These describe retained codec IDs, not audible repetition or a qualified threshold."},
        "cases": old["cases"], "temperature": 0.7, "subtalker_temperature": 0.7, "top_k": 50, "top_p": 1.0,
        "repetition_penalty": 1.05, "max_tokens": 1200, "total_budget_seconds": 600, "individual_budget_seconds": 120,
        "candidate": "Only sampleToken calls with a non-null eosTokenId; if the last eight generated IDs are identical and differ from EOS, set that ID's next logit to negative infinity after the original presence penalty. Do not change EOS, auxiliary sampling, RNG calls, full prefill/history, temperature/filter order, decoder or max length.",
        "selection": "Exactly the six previously frozen order/power-control cases, including the original failure twice. No new text, voice, seed or output-dependent cohort.",
        "baseline_reference": str(reference.resolve()), "baseline_reference_sha256": sha(reference),
        "legacy_baseline": str((ROOT.parent / "m4-acceptance-20261106/m1-text-layout-probe/report.json").resolve()),
        "original_plan_sha256": sha(original_plan), "model_directory": str(model.resolve()),
        "metallib": str((app / "Contents/Resources/mlx.metallib").resolve()),
        "sdk_source": str(sdk_source), "sdk_source_sha256": sha(sdk_source),
        "insertion_sha256": sha(ROOT / "insertion.swift.txt"),
        "app": str(app.resolve()), "execution": {"port": 18903, "request_timeout_seconds": 150,
            "whole_timeout_seconds": 900, "helper_sha256": build["files"]["Contents/Helpers/CyberAudioService"]["sha256"],
            "retries": 0, "serial": True, "maximum_owned_model_processes": 1},
        "qualification": [
            "Build first with zero model work. Run six fresh original baseline generations and require complete token/PCM identity with every retained reference before starting candidate inference.",
            "Run all six candidate generations once even if a termination result fails, within the original full 1200-token budget. Both failure repetitions must produce actual EOS, identical complete tokens/PCM, and preserve the original first47 tokens/first44 complete decoded frames.",
            "All four normal controls must preserve complete original token/PCM bytes. Do not select successful seeds, force EOS, shorten max length, rerun a failure or search other thresholds.",
            "Only if the prior necessary conditions pass, send all twelve baseline/candidate complete PCM artifacts through the accepted native ASR. Split each deterministically into <=24-second pieces; transcribe every piece with auto and the original producer language, concatenate in order, no retries or omissions.",
            "Both known-failure candidate outputs must exactly match the intended sentence under the frozen author mixed-token scorer in both ASR routes; every control must retain its baseline normalized content. ASR checks remain separate from human intelligibility.",
            "A passed pilot still does not promote a default or close full cohort, original strict M4, listening or other original gates. A failed necessary condition ends the pilot without a larger matrix."
        ],
        "frozen_results_and_production_preserved": True,
        "files_sha256": {name: sha(ROOT / name) for name in ["RepeatedCodeProbe.swift", "insertion.swift.txt", "prepare.py", "build.py", "run.py"]}}
for name in ["prepare.py", "build.py", "run.py"]:
    ast.parse((ROOT / name).read_text())
with (ROOT / "protocol.json").open("x") as output:
    json.dump(plan, output, ensure_ascii=False, indent=2); output.write("\n")
print(json.dumps({"status": "repeated_code_protocol_frozen_no_model", "cases_per_arm": 6,
                  "protocol_sha256": sha(ROOT / "protocol.json"), "formal_sources_changed": False}, indent=2))
