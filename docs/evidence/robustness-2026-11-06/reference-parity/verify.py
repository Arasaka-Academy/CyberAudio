"""Independently check capture manifests, comparisons, cleanup and preservation."""
from datetime import datetime, timezone
from pathlib import Path
import ast
import hashlib
import json
import struct
import subprocess
import urllib.request

base = Path(__file__).resolve().parent
project = base.parents[2]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
plan = read(base / "plan.json")
build = read(base / "native-build/build.json")
native = read(base / "native-capture/report.json")
reference = read(base / "reference-comparison.json")
runners = [read(base / name) for name in ["native-runner.json", "reference-runner.json"]]
assert all(r["status"] == "completed" and r["exit_code"] == 0 and r["owned_process_exit_code"] == 0 for r in runners)
assert all(r["production_preserved"] and r["sdk_and_helpers_preserved"] for r in runners)
assert native["status"] == reference["status"] == "diagnostic_completed"
assert native["plan_sha256"] == reference["plan_sha256"] == sha(base / "plan.json")
assert reference["native_report_sha256"] == sha(base / "native-capture/report.json")
assert sha(base / "requirements.lock") == runners[1]["requirements_sha256"]
assert sha(base / "compare-reference.py") == runners[1]["source_sha256"]
assert build["preserved_input_sha256"] == {p: sha(p) for p in build["preserved_input_sha256"]}
assert sha(base / "native-build/reference-parity-probe") == build["executable_sha256"]
assert reference["reference_source_sha256"] == plan["python_model_source_sha256"]
model_module = base / "runtime/lib/python3.13/site-packages/mlx_audio/tts/models/qwen3_tts/qwen3_tts.py"
assert sha(model_module) == plan["python_model_source_sha256"]
assert sha(base / "vendor/mlx_audio/tts/models/qwen3_tts/qwen3_tts.py") == plan["python_model_source_sha256"]
sizes, steps, arrays_count, comparisons, auxiliary = 0, 0, 0, 0, 0
summary = []
for case, capture, compared in zip(plan["cases"], native["results"], reference["results"], strict=True):
    assert case == capture["case"] == compared["case"]
    assert len(capture["steps"]) == len(compared["steps"]) == len(capture["tokens"])
    assert 0 < len(capture["steps"]) <= plan["max_steps_per_case"]
    for item in [capture["prepared"], *capture["steps"]]:
        path = base / "native-capture" / case["id"] / item["file"]
        data = path.read_bytes()
        assert len(data) == item["bytes"] and sha(path) == item["sha256"]
        sizes += len(data)
        header_length = struct.unpack("<Q", data[:8])[0]
        header = json.loads(data[8:8 + header_length])
        header.pop("__metadata__", None)
        assert set(header) == set(item["arrays"])
        arrays_count += len(header)
        for name, spec in header.items():
            assert spec["shape"] == item["arrays"][name]["shape"]
            begin, end = spec["data_offsets"]
            assert 0 <= begin <= end <= len(data) - header_length - 8
            width = {"BF16": 2, "F32": 4, "I32": 4, "U32": 4}[spec["dtype"]]
            elements = 1
            for dimension in spec["shape"]:
                elements *= dimension
            assert end - begin == elements * width
        if item["step"] >= 0:
            spec = header["codes"]
            assert spec["dtype"] in {"I32", "U32"} and spec["shape"] == [1, 16]
            begin, end = spec["data_offsets"]
            layout = "<16i" if spec["dtype"] == "I32" else "<16I"
            codes = list(struct.unpack(layout, data[8 + header_length + begin:8 + header_length + end]))
            assert codes == compared["steps"][item["step"]]["forced_codes"]
            assert codes[0] == capture["tokens"][item["step"]]
    audio_path = base / "native-capture" / case["id"] / capture["pcm_file"]
    assert audio_path.stat().st_size == capture["pcm_bytes"] and sha(audio_path) == capture["pcm_sha256"]
    for metric in [*compared["prepared"].values(), *(m for row in compared["steps"] for m in row["comparisons"].values())]:
        assert metric["shape_equal"] and metric["dtype_equal"] and metric["values_exact"]
        assert metric["max_abs"] == metric["mean_abs"] == metric["rms_abs"] == 0
        comparisons += 1
    for row in compared["steps"]:
        assert row["auxiliary_top1_equal"] == 15 and row["reference_eos"] == row["native_eos"]
        auxiliary += 15
    steps += len(capture["steps"])
    summary.append({"id": case["id"], "steps": len(capture["steps"]),
                    "eos_seen": capture["eos_seen"], "stop_reason": capture["stop_reason"],
                    "prepared_comparisons_exact": 4,
                    "step_comparisons_exact": 5 * len(capture["steps"]),
                    "maximum_absolute_error": 0})
assert sizes == native["snapshot_bytes"] <= plan["maximum_snapshot_bytes"]
baseline_path = project / ".context/robustness/m4-acceptance-20261106/m1-seeded-tts-probe/report.json"
original = read(baseline_path)["failure_observed"]
failed = native["results"][0]
assert failed["tokens"] == original["tokens"][:len(failed["tokens"])]
pcm = (base / "native-capture/known-failure/captured.pcm").read_bytes()
assert pcm == (baseline_path.parent / original["audio_file"]).read_bytes()[:len(pcm)]
assert failed["original_failure_token_prefix_matches"] and failed["original_failure_pcm_prefix_matches"]
source = read(project / "docs/evidence/robustness-2026-11-06/model-preservation/source-sha256.json")
assert source == {p: sha(project / p) for p in source}
preserved = read(project / ".context/robustness/model-preservation-20261107/fixed-result.json")
assert preserved["source_files_before"] == {p: sha(p) for p in preserved["source_files_before"]}
assert preserved["frozen_before"] == {p: sha(p) for p in preserved["frozen_before"]}
processes = subprocess.check_output(["/bin/ps", "-axo", "pid=,pgid=,command="], text=True).splitlines()
for runner in runners:
    pid = runner["owned_group_pid"]
    assert not any(str(pid) in row.split()[:2] for row in processes)
production = Path.home() / "Library/Application Support/CyberAudio"
with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
    state = json.load(response)
current = {"instance": state["instance"], "status": state["status"], "busy": state["busy"],
           "config_sha256": sha(production / "config/service.json"),
           "token_sha256": sha(production / "config/api-token")}
assert current == runners[0]["production_before"] == runners[1]["production_before"]
for path in base.glob("*.py"):
    ast.parse(path.read_text())
report = {"status": "verified", "verified_at": datetime.now(timezone.utc).isoformat(),
          "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
          "cases": summary, "total_steps": steps, "tensor_files": sum(1 + len(x["steps"]) for x in native["results"]),
          "tensor_arrays": arrays_count, "snapshot_bytes": sizes, "exact_comparisons": comparisons,
          "auxiliary_top1_agreements": auxiliary, "maximum_absolute_error": 0,
          "original_failure_token_and_pcm_prefix_exact": True, "owned_process_groups_absent": True,
          "current_source_hashes_verified": len(source), "original_model_files_verified": len(preserved["source_files_before"]),
          "frozen_files_verified": len(preserved["frozen_before"]), "production_preserved": True,
          "shared_sdk_and_objects_preserved": True, "unmodified_reference_source_verified": True,
          "independent_full_workload_rerun": False,
          "limits": ["At most 32 teacher-forced steps per case; no complete Python sampling/decode/content acceptance",
                     "Known failure parity is the captured prefix, not a new 1200-step failure reproduction",
                     "No M4 strict soak, complete public download, physical/listening or distribution gate acceptance"]}
(base / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report))
