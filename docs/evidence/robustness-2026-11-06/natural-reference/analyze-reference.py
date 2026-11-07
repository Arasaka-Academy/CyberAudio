"""File-only paired scoring; author tokenizer plus independent full-matrix distances."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
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
DATA = ROOT.parent / "post-delivery-20261107"
NATIVE = ROOT.parent / "natural-reference-20261107/native-check"
PLAN = json.loads((ROOT / "protocol.json").read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def full_matrix(expected, actual):
    table = [[0] * (len(actual) + 1) for _ in range(len(expected) + 1)]
    for i in range(len(expected) + 1):
        table[i][0] = i
    for j in range(len(actual) + 1):
        table[0][j] = j
    for i, left in enumerate(expected, 1):
        for j, right in enumerate(actual, 1):
            table[i][j] = min(table[i - 1][j] + 1, table[i][j - 1] + 1, table[i - 1][j - 1] + int(left != right))
    return table[-1][-1]


assert sha(DATA / "score-natural-asr.py") == PLAN["baseline"]["qualified_scorer_sha256"]
spec = importlib.util.spec_from_file_location("frozen_ascend_scorer", DATA / "score-natural-asr.py")
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)
self_tests = scorer.self_test()
runner = json.loads((ROOT / "runner.json").read_text())
reference_report = json.loads((ROOT / "reference.json").read_text())
assert runner["status"] == "reference_study_complete_pending_scoring"
assert runner["protocol_sha256"] == sha(ROOT / "protocol.json") == reference_report["protocol_sha256"]
assert all(runner[k] for k in ["production_preserved", "source_preserved", "protected_metadata_preserved", "scripts_preserved", "native_listener_closed"])
assert reference_report["status"] == "complete_reference_not_quality_accepted"
assert reference_report["retries"] == reference_report["skipped"] == 0
assert not reference_report["reference_methods_replaced"]
assert reference_report["results_sha256"] == sha(ROOT / "reference-results.private.jsonl")
assert sha(DATA / "natural-baseline/results.jsonl") == PLAN["baseline"]["raw_results_sha256"]
assert sha(DATA / "ascend-private/test-manifest.json") == PLAN["data"]["manifest_sha256"]
manifest = json.loads((DATA / "ascend-private/test-manifest.json").read_text())
native = list(map(json.loads, (DATA / "natural-baseline/results.jsonl").read_text().splitlines()))
reference = list(map(json.loads, (ROOT / "reference-results.private.jsonl").read_text().splitlines()))
assert len(manifest) == len(native) == len(reference) == 1315
assert [r["id"] for r in manifest] == [r["id"] for r in native] == [r["id"] for r in reference]
groups = {}
details = []
examples = []
for item, old, new in zip(manifest, native, reference):
    assert old["http_status"] == 200 and isinstance(old["response"]["text"], str)
    assert isinstance(new["response"]["text"], str)
    audio = DATA / "ascend-private" / item["audio"]
    assert sha(audio) == item["audio_sha256"] == new["audio_sha256"]
    with wave.open(str(audio), "rb") as sound:
        assert (sound.getnchannels(), sound.getsampwidth(), sound.getframerate(), sound.getnframes()) == (1, 2, 16000, item["frames"])
        pcm = sound.readframes(sound.getnframes())
    assert hashlib.sha256(pcm).hexdigest() == item["pcm_sha256"] == new["pcm_sha256"]
    # Python's standard array reproduces int16 -> float32 / 32768 exactly here,
    # independently of NumPy/MLX; division by a power of two is representable.
    import array
    import sys
    shorts = array.array("h", pcm)
    if sys.byteorder != "little":
        shorts.byteswap()
    values = array.array("f", (v / 32768 for v in shorts))
    if sys.byteorder != "little":
        values.byteswap()
    assert hashlib.sha256(values.tobytes()).hexdigest() == new["input_float32_sha256"]
    truth = scorer.mixed_tokens(item["reference"])
    before = scorer.mixed_tokens(old["response"]["text"])
    after = scorer.mixed_tokens(new["response"]["text"])
    before_edits = scorer.distance(truth, before)
    after_edits = scorer.distance(truth, after)
    assert full_matrix(truth, before) == before_edits
    assert full_matrix(truth, after) == after_edits
    char_truth = scorer.char_tokens(item["reference"])
    source_scripts = scorer.scripts(item["reference"])
    native_scripts = scorer.scripts(old["response"]["text"])
    reference_scripts = scorer.scripts(new["response"]["text"])
    row = {
        "id": item["id"], "language": item["language"],
        "speaker": item["original_speaker_id"], "session": item["session_id"],
        "reference": item["reference"], "native": old["response"], "python": new["response"],
        "native_edits": before_edits, "python_edits": after_edits, "reference_tokens": len(truth),
        "raw_equal": old["response"]["text"] == new["response"]["text"],
        "normalized_equal": before == after,
    }
    details.append(row)
    if item["id"] in PLAN["native_check"]["problem_ids"]:
        examples.append({k: row[k] for k in ["id", "language", "reference", "native", "python", "native_edits", "python_edits", "raw_equal", "normalized_equal"]})
    counts = {
        "utterances": 1, "reference_tokens": len(truth), "reference_characters": len(char_truth),
        "native_edits": before_edits, "python_edits": after_edits,
        "native_character_edits": scorer.distance(char_truth, scorer.char_tokens(old["response"]["text"])),
        "python_character_edits": scorer.distance(char_truth, scorer.char_tokens(new["response"]["text"])),
        "raw_equal": int(row["raw_equal"]), "normalized_equal": int(row["normalized_equal"]),
        "python_fewer_edits": int(after_edits < before_edits),
        "python_more_edits": int(after_edits > before_edits),
        "equal_edit_count": int(after_edits == before_edits),
        "native_normalized_exact": int(truth == before), "python_normalized_exact": int(truth == after),
        "native_empty": int(not before), "python_empty": int(not after),
        "native_missing_reference_chinese": int(source_scripts["chinese"] and not native_scripts["chinese"]),
        "python_missing_reference_chinese": int(source_scripts["chinese"] and not reference_scripts["chinese"]),
        "native_missing_reference_latin": int(source_scripts["latin"] and not native_scripts["latin"]),
        "python_missing_reference_latin": int(source_scripts["latin"] and not reference_scripts["latin"]),
    }
    for group in ["all", "language:" + item["language"], "speaker:" + str(item["original_speaker_id"]), "session:" + str(item["session_id"])]:
        groups.setdefault(group, Counter()).update(counts)
for group in groups.values():
    group["native_mer"] = group["native_edits"] / group["reference_tokens"]
    group["python_mer"] = group["python_edits"] / group["reference_tokens"]
    group["native_cer"] = group["native_character_edits"] / group["reference_characters"]
    group["python_cer"] = group["python_character_edits"] / group["reference_characters"]
baseline = json.loads((DATA / "natural-baseline/score.json").read_text())
for name, current in groups.items():
    previous = baseline["groups"][name]
    assert current["native_edits"] == previous["mixed_edits"]
    assert current["reference_tokens"] == previous["mixed_reference_tokens"]
    assert current["native_character_edits"] == previous["character_edits"]
assert len(examples) == 4
sources = json.loads((ROOT / "source-before.json").read_text())
assert all(sha(PROJECT / p) == digest for p, digest in sources.items())
assert all(sha(PROJECT / p) == digest for p, digest in PLAN["reference_source_sha256"].items())
metadata = json.loads((ROOT / "protected-metadata-before.private.json").read_text())
assert all([Path(p).stat().st_size, Path(p).stat().st_mtime_ns] == stat for p, stat in metadata.items())
assert runner["scripts_sha256"] == {p.name: sha(p) for p in sorted(ROOT.glob("*.py"))}
native_runner = json.loads((NATIVE / "runner.json").read_text())
owned = [s["owned_group_pid"] for s in runner["steps"]] + [native_runner["owned_helper_pid"]]
for pid in owned:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        continue
    raise AssertionError("Owned process remains alive: " + str(pid))
with socket.socket() as probe:
    assert probe.connect_ex(("127.0.0.1", PLAN["native_check"]["port"])) != 0
assert not list((NATIVE / "service/tmp").glob("request-*"))
production = Path.home() / "Library/Application Support/CyberAudio"
with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
    state = json.load(response)
assert {"instance": state["instance"], "status": state["status"], "config_sha256": sha(production / "config/service.json"), "token_sha256": sha(production / "config/api-token")} == runner["production_before"]
report = {
    "status": "complete_paired_reference_verified_not_quality_accepted",
    "verified_at": datetime.now(timezone.utc).isoformat(),
    "protocol_sha256": sha(ROOT / "protocol.json"), "source_commit": PLAN["source_commit"],
    "input_count": len(manifest), "input_wav_pcm_float32_verified": len(manifest),
    "independent_full_matrix_distances_verified": 2 * len(manifest),
    "frozen_native_baseline_totals_reproduced": True, "native_repeat_check": runner["native_baseline_reproduced"],
    "self_tests": self_tests, "groups": groups, "examples": examples,
    "preservation": {"tracked_sources": len(sources), "model_and_frozen_file_metadata": len(metadata), "production": True, "sources": True, "reference_sources": True, "owned_processes_absent": True, "native_listener_closed": True, "temporary_request_count": 0},
    "source_sha256": {"analyze.py": sha(__file__), "qualified_scorer": sha(DATA / "score-natural-asr.py")},
    "evidence_sha256": {p: sha(ROOT / p) for p in ["protocol.json", "runner.json", "reference.json", "reference-results.private.jsonl", "native-check-qualification.json"]},
    "limits": [
        "Reference agreement is not a correctness or listening criterion; shared errors remain product-quality gaps.",
        "A ten-case native repeat supports reuse of the fixed native baseline; it is not another full native run.",
        "Reference-versus-native numerical differences may affect other cases independently of shared observed errors.",
        "Script absence is a triage indicator and does not count confirmed semantic failures.",
        "No prompt, language, gain, padding, segmentation, model or production-default candidate was tested or promoted.",
        "Full M4 soak, physical/listening, complete public downloads, Developer ID and live GUI upgrade gates remain open.",
    ],
    "data_license": "CC-BY-SA-4.0", "data_attribution": "ASCEND, Lovenia et al., LREC 2022, HKUST CAiRE",
}
assert not (ROOT / "paired.private.jsonl").exists() and not (ROOT / "summary.json").exists()
(ROOT / "paired.private.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in details))
(ROOT / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"status": report["status"], "all": groups["all"], "mixed": groups["language:mixed"], "examples": [{"id": e["id"], "raw_equal": e["raw_equal"], "native_edits": e["native_edits"], "python_edits": e["python_edits"]} for e in examples]}, ensure_ascii=False, indent=2))
