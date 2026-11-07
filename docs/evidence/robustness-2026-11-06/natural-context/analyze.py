"""File-only paired analysis and independent distance/preservation checks."""
from datetime import datetime, timezone
from pathlib import Path
import array
import hashlib
import importlib.util
import json
import re
import struct
import subprocess
import sys
import urllib.request
import wave

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
DATA = ROOT.parent / "post-delivery-20261107"
PLAN = json.loads((ROOT / "protocol.json").read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def independent_distance(a, b):
    matrix = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        matrix[i][0] = i
    for j in range(len(b) + 1):
        matrix[0][j] = j
    for i, left in enumerate(a, 1):
        for j, right in enumerate(b, 1):
            matrix[i][j] = min(matrix[i - 1][j] + 1, matrix[i][j - 1] + 1,
                               matrix[i - 1][j - 1] + (left != right))
    return matrix[-1][-1]


def is_subsequence(expected, actual):
    actual = iter(actual)
    return all(any(a == e for a in actual) for e in expected)


assert sha(ROOT / "protocol.json") == "2c2fc24f2f73af22d8aae1014af549983e41ca53f1173b5ca8940b489bedf411"
scorer_path = PROJECT / PLAN["analysis"]["scorer_path"]
assert sha(scorer_path) == PLAN["analysis"]["scorer_sha256"]
spec = importlib.util.spec_from_file_location("qualified_ascend_scorer", scorer_path)
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)
scorer.self_test()
runner = json.loads((ROOT / "runner.json").read_text())
inference = json.loads((ROOT / "inference.json").read_text())
assert runner["status"] == inference["status"] == "pilot_complete_pending_scoring"
assert runner["owned_exit_code"] == 0 and runner["owned_group_absent"]
assert inference["baseline_matches"] == 10 and inference["completed_generations"] == 20
assert sha(ROOT / "results.private.jsonl") == inference["results_sha256"]
for name, digest in runner["scripts_sha256"].items():
    assert sha(ROOT / name) == digest
for relative, digest in PLAN["reference_source_sha256"].items():
    assert sha(PROJECT / relative) == digest
manifest_path = DATA / "ascend-private/test-manifest.json"
assert sha(manifest_path) == PLAN["data"]["manifest_sha256"]
manifest = {r["id"]: r for r in json.loads(manifest_path.read_text())}
prior_path = PROJECT / PLAN["baseline"]["relative_path"]
assert sha(prior_path) == PLAN["baseline"]["sha256"]
prior = {r["id"]: r for r in map(json.loads, prior_path.read_text().splitlines())}
rows = list(map(json.loads, (ROOT / "results.private.jsonl").read_text().splitlines()))
assert [(r["arm"], r["id"]) for r in rows] == [(arm, i) for arm in PLAN["execution"]["arms"] for i in PLAN["data"]["ids"]]
arms = {arm: {r["id"]: r for r in rows if r["arm"] == arm} for arm in PLAN["execution"]["arms"]}
han = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
pairs = []
distances = 0
assert sys.byteorder == "little"
for identity in PLAN["data"]["ids"]:
    item = manifest[identity]
    audio = DATA / "ascend-private" / item["audio"]
    assert sha(audio) == item["audio_sha256"]
    with wave.open(str(audio), "rb") as sound:
        assert (sound.getnchannels(), sound.getsampwidth(), sound.getframerate(), sound.getnframes()) == (1, 2, 16000, item["frames"])
        pcm = sound.readframes(sound.getnframes())
    assert hashlib.sha256(pcm).hexdigest() == item["pcm_sha256"]
    values = array.array("f", (sample / 32768 for (sample,) in struct.iter_unpack("<h", pcm)))
    digest = hashlib.sha256(values.tobytes()).hexdigest()
    pair = {"id": identity, "language": item["language"], "reference": item["reference"], "arms": {}}
    ref_tokens = scorer.mixed_tokens(item["reference"])
    for arm in PLAN["execution"]["arms"]:
        row = arms[arm][identity]
        assert row["audio_sha256"] == item["audio_sha256"] and row["pcm_sha256"] == item["pcm_sha256"]
        assert row["input_float32_sha256"] == digest == prior[identity]["input_float32_sha256"]
        response = row["response"]
        if arm == "baseline-empty-context":
            keys = PLAN["baseline"]["compare_fields"]
            assert {k: response[k] for k in keys} == {k: prior[identity]["response"][k] for k in keys}
        tokens = scorer.mixed_tokens(response["text"])
        edits = scorer.distance(ref_tokens, tokens)
        assert edits == independent_distance(ref_tokens, tokens)
        distances += 1
        pair["arms"][arm] = {"text": response["text"], "language": response["language"],
                              "mixed_edits": edits, "normalized_exact": tokens == ref_tokens,
                              "generation_tokens": response["generation_tokens"]}
    baseline = pair["arms"]["baseline-empty-context"]
    candidate = pair["arms"]["fixed-verbatim-context"]
    pair["candidate_edit_delta"] = candidate["mixed_edits"] - baseline["mixed_edits"]
    pair["raw_text_equal"] = candidate["text"] == baseline["text"]
    pair["original_han_preserved_in_order"] = is_subsequence(han.findall(item["reference"]), han.findall(candidate["text"]))
    pair["original_english_words_preserved_in_order"] = is_subsequence([t for t in ref_tokens if t.isascii()], [t for t in scorer.mixed_tokens(candidate["text"]) if t.isascii()])
    pairs.append(pair)
targets = [p for p in pairs if p["id"] in PLAN["data"]["translation_ids"]]
controls = [p for p in pairs if p["id"] not in PLAN["data"]["translation_ids"]]
target_pass = all(p["candidate_edit_delta"] < 0 and p["original_han_preserved_in_order"] and p["original_english_words_preserved_in_order"] for p in targets)
control_pass = all(p["candidate_edit_delta"] <= 0 for p in controls)
qualified = target_pass and control_pass
sources = json.loads((ROOT / "source-before.json").read_text())
assert {p: sha(PROJECT / p) for p in sources} == sources
metadata = json.loads((ROOT / "protected-metadata-before.private.json").read_text())
assert {p: [Path(p).stat().st_size, Path(p).stat().st_mtime_ns] for p in metadata} == metadata
before = json.loads((ROOT / "production-before.private.json").read_text())
production = Path.home() / "Library/Application Support/CyberAudio"
with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
    state = json.load(response)
assert state["status"] == "ready" and not state["busy"]
assert before == {"instance": state["instance"], "status": state["status"],
                  "config_sha256": sha(production / "config/service.json"), "token_sha256": sha(production / "config/api-token")}
groups = [int(row.split()[0]) for row in subprocess.check_output(["/bin/ps", "-axo", "pgid=,pid="], text=True).splitlines() if row.strip()]
assert runner["owned_group_pid"] not in groups
summary = {
    "status": "pilot_qualified_for_separate_native_study" if qualified else "pilot_closed_without_qualification",
    "at": datetime.now(timezone.utc).isoformat(), "protocol_sha256": sha(ROOT / "protocol.json"),
    "results_sha256": sha(ROOT / "results.private.jsonl"), "analyzer_sha256": sha(__file__),
    "completed_generations": len(rows), "fresh_baselines_reproduced": inference["baseline_matches"],
    "independently_recomputed_mixed_distances": distances, "raw_text_equal_pairs": sum(p["raw_text_equal"] for p in pairs),
    "baseline_edits": sum(p["arms"]["baseline-empty-context"]["mixed_edits"] for p in pairs),
    "candidate_edits": sum(p["arms"]["fixed-verbatim-context"]["mixed_edits"] for p in pairs),
    "pairs_improved": sum(p["candidate_edit_delta"] < 0 for p in pairs),
    "pairs_equal_edits": sum(p["candidate_edit_delta"] == 0 for p in pairs),
    "pairs_worsened": sum(p["candidate_edit_delta"] > 0 for p in pairs),
    "both_translation_targets_qualified": target_pass, "all_controls_nonregressed": control_pass,
    "candidate_promoted": False, "production_defaults_changed": False, "quality_accepted": False,
    "preservation": {"tracked_sources": len(sources), "protected_metadata_files": len(metadata),
                     "production_ready_idle_config_token_preserved": True, "owned_group_absent": True},
    "known_cases": [p for p in pairs if p["id"] in PLAN["data"]["translation_ids"] + PLAN["data"]["other_retained_failure_ids"]],
    "next_action": "Freeze a separate native/full-cohort protocol before more inference" if qualified else "Pilot closed: do not broaden, retune context, or change production defaults",
    "limits": ["A diagnostic selection of ten examples is not a population quality benchmark.",
               "Python generation timing is not native HTTP or microphone end-to-end latency.",
               "All original natural-quality, strict M4, physical, public-download and distribution gates remain open."],
}
(ROOT / "pairs.private.jsonl").write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in pairs))
(ROOT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(summary, ensure_ascii=False, indent=2))
