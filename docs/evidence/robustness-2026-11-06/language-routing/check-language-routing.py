"""Check deterministic actual-model language routing; never infer listening quality."""
from pathlib import Path
import hashlib
import json
import sys

report_path, receipt_path = map(Path, sys.argv[1:3])
report = json.loads(report_path.read_text())
assert report["status"] == "diagnostic_completed" and report["completed_generations"] == 4
reference = json.loads(Path(sys.argv[3]).read_text()) if len(sys.argv) == 4 else None
checks = []
for voice in ["eric", "dylan"]:
    rows = {r["language"]: r for r in report["results"] if r["voice"] == voice}
    assert set(rows) == {"english", "chinese"}
    for row in rows.values():
        path = report_path.parent / (row["id"] + ".pcm")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["pcm_sha256"]
        assert row["eos_seen"] and row["text"] == "1234" and row["seed"] == 31
    changed = rows["english"]["pcm_sha256"] != rows["chinese"]["pcm_sha256"] and rows["english"]["tokens_sha256"] != rows["chinese"]["tokens_sha256"]
    checks.append({"voice": voice, "check": "explicit English changes actual tokens and PCM from Chinese", "passed": changed})
    if reference:
        previous = next(r for r in reference["results"] if r["voice"] == voice and r["language"] == "chinese")
        checks.append({"voice": voice, "check": "Chinese dialect token and PCM output preserved", "passed": all(rows["chinese"][key] == previous[key] for key in ["tokens_sha256", "pcm_sha256"])})
failures = sum(not r["passed"] for r in checks)
receipt = {"status": "passed" if not failures else "failed", "failures": failures, "checks": checks,
           "source_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(), "scope": "Deterministic language-selection boundary only; not a general speech/listening score"}
assert not receipt_path.exists()
receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt, indent=2))
raise SystemExit(1 if failures else 0)
