"""Export explicit synthetic-diagnostic records, preserving all private originals."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
DEST = PROJECT / "docs/evidence/robustness-2026-11-06/repeated-code"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


pilot = json.loads((ROOT / "summary.private.json").read_text())
matrix = json.loads((ROOT / "matrix-summary.private.json").read_text())
assert pilot["status"] == "fixed_repeated_code_pilot_verified"
assert matrix["status"] == "fixed_repeated_code_full_cohort_verified"
assert matrix["production_preserved"] and matrix["owned_model_processes_absent"] and matrix["owned_listener_closed"]
if DEST.exists():
    assert sorted(path.name for path in DEST.iterdir()) == ["README.md"], "Preserve any prior export"
else:
    DEST.mkdir(exist_ok=False)
removed = {"production_before", "production_after", "startup", "protected_file_metadata_before", "owned_identity", "token_sha256", "config_sha256"}


def public(value):
    if isinstance(value, dict):
        return {public(key): public(item) for key, item in value.items() if key not in removed}
    if isinstance(value, list):
        return [public(item) for item in value]
    if isinstance(value, str):
        return value.replace(str(PROJECT), "<workspace>").replace(str(Path.home()), "<home>")
    return value


records = []
json_files = {
    "protocol.json": "protocol.json", "matrix-plan.json": "matrix-plan.json",
    "build/build.json": "pilot-build.json", "runner.json": "pilot-runner.json",
    "baseline/report.json": "pilot-baseline.json", "candidate/report.json": "pilot-candidate.json",
    "roundtrip-http/runner.json": "pilot-asr-runner.json", "roundtrip-manifest.json": "pilot-asr-manifest.json",
    "roundtrip-transform.json": "pilot-audio-transforms.json", "roundtrip-implementation.json": "pilot-asr-implementation.json",
    "summary.private.json": "pilot-summary.json", "verification-refresh.json": "pilot-verification-refresh.json",
    "matrix-build/build.json": "matrix-build.json", "matrix-runner.json": "matrix-runner.json",
    "matrix-candidate/report.json": "matrix-generations.json", "matrix-summary.private.json": "matrix-summary.json",
    "matrix-pairs.private.json": "matrix-pairs.json", "matrix-analysis-protocol.json": "matrix-analysis-protocol.json",
    "matrix-roundtrip-http/runner.json": "matrix-asr-runner.json", "matrix-roundtrip-manifest.json": "matrix-asr-manifest.json",
    "matrix-roundtrip-transform.json": "matrix-audio-transforms.json", "matrix-roundtrip-implementation.json": "matrix-asr-implementation.json",
    "source-before.json": "source-before-pilot.json", "matrix-source-before.json": "source-before-matrix.json",
    "../short-and-transport-20261107/ci-verification.json": "baseline-ci-verification.json",
}
for source, target in json_files.items():
    original = ROOT / source
    value = public(json.loads(original.read_text()))
    (DEST / target).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    records.append({"source": source, "source_sha256": sha(original), "export": target, "export_sha256": sha(DEST / target), "format": "public_normalized_json"})
for source, target in [("roundtrip-http/results.jsonl", "pilot-asr-results.json"), ("matrix-roundtrip-http/results.jsonl", "matrix-asr-results.json")]:
    original = ROOT / source
    (DEST / target).write_text(json.dumps(public([json.loads(line) for line in original.read_text().splitlines()]), ensure_ascii=False, indent=2) + "\n")
    records.append({"source": source, "source_sha256": sha(original), "export": target, "export_sha256": sha(DEST / target), "format": "public_json_array_from_jsonl"})
snippet = ROOT / "insertion.swift.txt"
(DEST / "insertion.json").write_text(json.dumps({"source_sha256": sha(snippet), "source_text": snippet.read_text()}, indent=2) + "\n")
records.append({"source": "insertion.swift.txt", "source_sha256": sha(snippet), "export": "insertion.json", "export_sha256": sha(DEST / "insertion.json"), "format": "literal_source_in_json"})
sources = ["RepeatedCodeProbe.swift", "RepeatedCodeMatrixProbe.swift", "prepare.py", "build.py", "run.py", "prepare-roundtrip.py", "run-roundtrip.py", "analyze.py", "prepare-matrix.py", "build-matrix.py", "run-matrix.py", "prepare-matrix-roundtrip.py", "run-matrix-roundtrip.py", "analyze-matrix.py", "export-evidence.py"]
for name in sources:
    assert str(Path.home()) not in (ROOT / name).read_text()
    shutil.copyfile(ROOT / name, DEST / name)
    records.append({"source": name, "source_sha256": sha(ROOT / name), "export": name, "export_sha256": sha(DEST / name), "format": "exact_source_copy"})
index = {"exported_at": datetime.now(timezone.utc).isoformat(), "source_commit": json.loads((ROOT / "protocol.json").read_text())["source_commit"],
         "records": records, "normalization": "JSON home/workspace paths normalized; production snapshots, identity/config/token fields omitted. Original hashes refer to retained private bytes. Source copies are exact; no audio, weights, binaries or logs exported."}
(DEST / "export-record.json").write_text(json.dumps(index, indent=2) + "\n")
print(json.dumps({"status": "synthetic_diagnostic_evidence_exported", "files": len(records)+1, "directory": str(DEST)}))
