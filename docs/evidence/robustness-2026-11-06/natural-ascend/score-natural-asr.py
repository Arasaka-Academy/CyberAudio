"""Private ASCEND baseline scorer; no inference or production writes.

Tokenization/normalization follow the inspected MIT-licensed HLTCHKUST/ASCEND
utils.py and eval.py at 8c50c2e2c65f8555eb1d7655310dd585dc1f6710.
ASCEND references and derived per-utterance text remain CC-BY-SA-4.0,
attributed to Lovenia et al., LREC 2022, HKUST CAiRE.
"""
import ast
import hashlib
import importlib.metadata
import json
import logging
import math
import re
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "scoring-packages"))
import jieba

assert importlib.metadata.version("jieba") == "0.42.1"
jieba.setLogLevel(logging.ERROR)
(BASE / "scorer-cache").mkdir(exist_ok=True)
jieba.dt.tmp_dir = str(BASE / "scorer-cache")
source = (BASE / "author-utils.private").read_bytes()
assert hashlib.sha256(source).hexdigest() == "fb70e35cf1f350adfd5e64c76cd319b4c5543b6cf39edfed15749560e3b82f7d"
# Read only the constant literal, without importing/executing external scripts.
assignment = next(
    node for node in ast.parse(source).body
    if isinstance(node, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id == "CHARS_TO_IGNORE" for t in node.targets)
)
IGNORE = ast.literal_eval(assignment.value)
assert isinstance(IGNORE, list) and all(isinstance(c, str) for c in IGNORE)
IGNORE_RE = re.compile("[" + re.escape("".join(IGNORE)) + "]")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalize(text):
    return IGNORE_RE.sub("", text).lower() + " "


def mixed_tokens(text):
    # Same operations and default jieba HMM behavior as the author function.
    words = [token for token in jieba.lcut(normalize(text)) if token.strip()]
    return [piece for token in words for piece in ([token] if token.isascii() else list(token))]


def char_tokens(text):
    return [c for c in normalize(text) if c.strip()]


def distance(reference, hypothesis):
    previous = list(range(len(hypothesis) + 1))
    for i, expected in enumerate(reference, 1):
        current = [i]
        for j, actual in enumerate(hypothesis, 1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (expected != actual),
            ))
        previous = current
    return previous[-1]


def scripts(text):
    return {
        "chinese": bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text)),
        "latin": bool(re.search("[a-zA-Z]", text)),
    }


def percentile(values, q):
    values = sorted(values)
    at = (len(values) - 1) * q
    lo, hi = math.floor(at), math.ceil(at)
    return values[lo] + (values[hi] - values[lo]) * (at - lo)


def self_test():
    token_cases = [
        ("你好，WORLD！", ["你", "好", "world"]),
        ("今天 We meet Alice at 3.5 p.m.。", ["今", "天", "we", "meet", "alice", "at", "35", "pm"]),
        ("繁體中文", ["繁", "體", "中", "文"]),
        ("嗯 um 嗯", ["嗯", "um", "嗯"]),
        ("Ａ １２", ["ａ", "１", "２"]),
        ("1234", ["1234"]),
        ("，。！", []),
    ]
    for text, expected in token_cases:
        assert mixed_tokens(text) == expected, (text, mixed_tokens(text), expected)
    cases = [
        ([], [], 0), ([], ["a"], 1), (["a"], [], 1),
        (["你", "好"], ["你", "们"], 1),
        (["a", "b", "c"], ["a", "c"], 1),
        (["a", "c"], ["a", "b", "c"], 1),
        (list("kitten"), list("sitting"), 3),
        (list("saturday"), list("sunday"), 3),
    ]
    for reference, hypothesis, expected in cases:
        assert distance(reference, hypothesis) == expected
    assert char_tokens("Hi， 你好！") == ["h", "i", "你", "好"]
    assert scripts("METI 与三") == {"chinese": True, "latin": True}
    assert mixed_tokens("three") != mixed_tokens("3")
    assert mixed_tokens("体") != mixed_tokens("體")
    return {"token_cases": len(token_cases), "distance_cases": len(cases), "extra_checks": 4}


def main():
    checks = self_test()
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        print(json.dumps({"status": "passed", **checks}))
        return
    manifest_path = BASE / "ascend-private/test-manifest.json"
    result_path = BASE / "natural-baseline/results.jsonl"
    manifest = json.loads(manifest_path.read_text())
    outputs = [json.loads(line) for line in result_path.read_text().splitlines()]
    assert len(manifest) == len(outputs) == 1315
    assert [r["id"] for r in manifest] == [r["id"] for r in outputs]
    assert all(r["http_status"] == 200 and isinstance(r["response"]["text"], str) for r in outputs)
    groups = {}
    rows = []
    latency = []
    for item, output in zip(manifest, outputs):
        reference, hypothesis = item["reference"], output["response"]["text"]
        ref_tokens, hyp_tokens = mixed_tokens(reference), mixed_tokens(hypothesis)
        ref_chars, hyp_chars = char_tokens(reference), char_tokens(hypothesis)
        ref_scripts, hyp_scripts = scripts(reference), scripts(hypothesis)
        row = {
            "id": item["id"], "language": item["language"],
            "speaker": item["original_speaker_id"], "session": item["session_id"],
            "reference": reference, "hypothesis": hypothesis,
            "reference_tokens": ref_tokens, "hypothesis_tokens": hyp_tokens,
            "mixed_edits": distance(ref_tokens, hyp_tokens), "mixed_reference_tokens": len(ref_tokens),
            "character_edits": distance(ref_chars, hyp_chars), "reference_characters": len(ref_chars),
            "normalized_exact": ref_tokens == hyp_tokens,
            "empty_hypothesis": not hyp_tokens,
            "reference_scripts": ref_scripts, "hypothesis_scripts": hyp_scripts,
            "duration_seconds": item["duration_seconds"],
            "request_seconds": output["elapsed_seconds"],
        }
        rows.append(row)
        latency.append(row["request_seconds"])
        for group in ["all", "language:" + item["language"],
                      "speaker:" + str(item["original_speaker_id"]), "session:" + str(item["session_id"])]:
            count = groups.setdefault(group, Counter())
            count.update({
                "utterances": 1,
                "mixed_edits": row["mixed_edits"], "mixed_reference_tokens": len(ref_tokens),
                "character_edits": row["character_edits"], "reference_characters": len(ref_chars),
                "normalized_exact": int(row["normalized_exact"]),
                "empty_hypotheses": int(row["empty_hypothesis"]),
                "duration_seconds": item["duration_seconds"], "request_seconds": row["request_seconds"],
                "references_with_both_scripts": int(all(ref_scripts.values())),
                "both_script_references_missing_a_script_in_hypothesis": int(all(ref_scripts.values()) and not all(hyp_scripts.values())),
            })
    for group, count in groups.items():
        count["mixed_error_rate"] = count["mixed_edits"] / count["mixed_reference_tokens"] if count["mixed_reference_tokens"] else None
        count["character_error_rate"] = count["character_edits"] / count["reference_characters"] if count["reference_characters"] else None
        count["request_real_time_factor"] = count["request_seconds"] / count["duration_seconds"]
    per_row = BASE / "natural-baseline/scored-utterances.private.jsonl"
    per_row.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    report = {
        "status": "baseline_measured_not_quality_accepted",
        "protocol_sha256": sha(BASE / "natural-asr-protocol.json"),
        "manifest_sha256": sha(manifest_path), "raw_outputs_sha256": sha(result_path),
        "per_utterance_scores_sha256": sha(per_row), "scorer_sha256": sha(__file__),
        "jieba_version": importlib.metadata.version("jieba"),
        "jieba_dictionary_sha256": sha(BASE / "scoring-packages/jieba/dict.txt"),
        "self_tests": checks, "groups": groups,
        "request_latency_seconds": {"p50": percentile(latency, .5), "p95": percentile(latency, .95), "maximum": max(latency)},
        "limitations": [
            "This unmodified-model baseline establishes no new pass threshold and closes no original quality gate.",
            "Script absence is a triage indicator; numbers, names, transliteration and valid translations require separate review.",
            "Normalization retains digit spelling, traditional Chinese, fillers and reference annotation quirks.",
            "Request latency includes native decoding and HTTP; this is not comparable to third-party decoder-only timings.",
            "No physical microphone or human listening was performed.",
        ],
        "attribution": "ASCEND, Lovenia et al., LREC 2022, HKUST CAiRE. References and derived per-utterance text: CC-BY-SA-4.0.",
    }
    (BASE / "natural-baseline/score.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "all": groups["all"], "mixed": groups.get("language:mixed"), "latency": report["request_latency_seconds"]}, indent=2))


if __name__ == "__main__":
    main()
