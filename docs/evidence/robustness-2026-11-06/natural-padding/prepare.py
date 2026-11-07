"""Freeze one silence-placement pilot before loading any model."""
import hashlib
import json
import os
import subprocess
import wave
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
DATA = ROOT.parent / "post-delivery-20261107"
APP = ROOT.parent / "readiness-ownership-20261107/final-bytes-readiness/CyberAudio.app"
PROBLEM = "ascend_ses1_spk17_L4213_465.2000_0.6000"
BUILD = json.loads((APP.parent / "build.json").read_text())


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def put(name, value):
    with (ROOT / name).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


assert not (ROOT / "protocol.json").exists(), "Preserve the frozen pilot"
os.chmod(ROOT, 0o700)
manifest_path = DATA / "ascend-private/test-manifest.json"
manifest = json.loads(manifest_path.read_text())
assert len(manifest) == len({row["id"] for row in manifest}) == 1315
assert sha(manifest_path) == json.loads((DATA / "ascend-private/preparation.json").read_text())["manifest_sha256"]
eligible = [row for row in manifest if row["frames"] < 16000]
controls = []
for language in ["en", "mixed", "zh"]:
    rows = sorted((row for row in eligible if row["language"] == language and row["id"] != PROBLEM), key=lambda row: row["id"])
    assert len(rows) >= 2
    controls += [rows[0], rows[-1]]
ids = {PROBLEM, *(row["id"] for row in controls)}
selected = [row for row in manifest if row["id"] in ids]
assert len(selected) == 7
audio_dir = ROOT / "audio"
audio_dir.mkdir(mode=0o700, exist_ok=False)
cases_by_arm = {name: [] for name in ["original", "trailing", "leading"]}
transforms = []
for row in selected:
    source = DATA / "ascend-private" / row["audio"]
    assert sha(source) == row["audio_sha256"]
    with wave.open(str(source), "rb") as sound:
        assert (sound.getnchannels(), sound.getsampwidth(), sound.getframerate()) == (1, 2, 16000)
        assert sound.getnframes() == row["frames"] < 16000
        original = sound.readframes(sound.getnframes())
    assert hashlib.sha256(original).hexdigest() == row["pcm_sha256"]
    zeros = bytes(2 * (16000 - row["frames"]))
    for arm, pcm in [("original", original), ("trailing", original + zeros), ("leading", zeros + original)]:
        path = source
        if arm != "original":
            path = audio_dir / (row["id"] + "-" + arm + ".wav")
            with path.open("xb") as stream:
                with wave.open(stream, "wb") as sound:
                    sound.setparams((1, 2, 16000, len(pcm) // 2, "NONE", "not compressed"))
                    sound.writeframes(pcm)
        decoded = subprocess.run(
            ["/opt/homebrew/bin/ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-protocol_whitelist", "file,pipe", "-format_whitelist", "wav", "-i", str(path),
             "-map", "0:a:0", "-vn", "-t", "60.1", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"],
            capture_output=True, check=True, timeout=15,
        )
        assert decoded.stdout == pcm, "FFmpeg must preserve every PCM sample"
        cases_by_arm[arm].append({
            "id": row["id"] + "-" + arm, "source_id": row["id"], "arm": arm,
            "role": "retained_empty_case" if row["id"] == PROBLEM else "metadata_selected_short_control",
            "language": row["language"], "reference": row["reference"],
            "audio": str(path.resolve()), "audio_sha256": sha(path),
            "pcm_sha256": hashlib.sha256(pcm).hexdigest(), "frames": len(pcm) // 2,
            "original_frames": row["frames"], "original_audio_sha256": row["audio_sha256"],
        })
        transforms.append({"id": row["id"] + "-" + arm, "ffmpeg_pcm_exact": True, "frames": len(pcm) // 2})
cases = [row for arm in ["original", "trailing", "leading"] for row in cases_by_arm[arm]]
old = (ROOT.parent / "natural-level-20261107/run.py").read_text()


def replace_once(before, after):
    global old
    assert old.count(before) == 1, before
    old = old.replace(before, after)


replace_once('"""Run the fixed seven-recording input-level causal protocol once."""',
             '"""Run the frozen original/trailing/leading pilot once with baseline gates."""')
replace_once('APP = PROJECT / ".context/robustness/stream-failure-20261107/final-bytes-stream-failure/CyberAudio.app"',
             'APP = Path(STUDY_PROTOCOL["app"])')
replace_once('assert sha(HELPER) == PROTOCOL["helper_sha256"] == BUILD["files"]["Contents/Helpers/CyberAudioService"]["sha256"]',
             'assert sha(HELPER) == STUDY_PROTOCOL["execution"]["helper_sha256"] == BUILD["files"]["Contents/Helpers/CyberAudioService"]["sha256"]')
replace_once('["protocol.json", "run.py", "transform-verification.json"]',
             '["protocol.json", "run.py", "prepare.py", "analyze.py", "transform-verification.json"]')
replace_once('    token = (SERVICE / "config/api-token").read_text().strip()',
             '    original_outputs = {row["id"]: row["response"] for row in map(json.loads, (BASE / "natural-baseline/results.jsonl").read_text().splitlines())}\n'
             '    parity_count = {"original": 0, "trailing": 0}\n'
             '    token = (SERVICE / "config/api-token").read_text().strip()')
replace_once('                audio = BASE / "ascend-private" / row["audio"]',
             '                if row["arm"] == "trailing":\n'
             '                    assert parity_count["original"] == 7, "Original parity must qualify before explicit padding"\n'
             '                if row["arm"] == "leading":\n'
             '                    assert parity_count == {"original": 7, "trailing": 7}, "Both control arms must qualify before leading silence"\n'
             '                audio = Path(row["audio"])')
replace_once('                if i % 7 == 0:',
             '                assert abs(response["duration"] - row["frames"] / 16000) < 1e-8\n'
             '                if row["arm"] in parity_count:\n'
             '                    keys = ["text", "language", "no_speech", "model", "repository", "revision"]\n'
             '                    expected = original_outputs[row["source_id"]]\n'
             '                    assert {k: response[k] for k in keys} == {k: expected[k] for k in keys}, "Baseline text/language/model parity failed: " + row["id"]\n'
             '                    parity_count[row["arm"]] += 1\n'
             '                    report["parity_counts"] = parity_count.copy()\n'
             '                    save()\n'
             '                if i % 7 == 0:')
with (ROOT / "run.py").open("x") as stream:
    stream.write(old)
compile(old, str(ROOT / "run.py"), "exec")
put("transform-verification.json", {"model_loaded": False, "cases": transforms, "all_ffmpeg_pcm_exact": True})
protocol = {
    "created_at": datetime.now(timezone.utc).isoformat(),
    "purpose": "One fixed causal pilot of zero-padding placement on the retained short empty-ASR case",
    "source_commit": subprocess.check_output(["rtk", "proxy", "git", "rev-parse", "HEAD"], cwd=PROJECT, text=True).strip(),
    "hypothesis": "With total effective waveform length fixed at 16000 samples, moving only the existing minimum-length zeros from the tail to the head may change the shared empty output. This does not assume clipping or missing audio.",
    "fixed_problem_id": PROBLEM,
    "control_selection": "All 1315 original rows with PCM frames <16000, excluding the retained problem; first and last lexicographic ID within each metadata language en/mixed/zh; no output, amplitude or reference-dependent selection",
    "eligible_count": len(eligible), "eligible_language_counts": dict(Counter(row["language"] for row in eligible)),
    "selected_original_count": 7, "expected_requests": 21,
    "app": str(APP.resolve()), "helper_source_commit": BUILD["source_commit"],
    "manifest_sha256": sha(manifest_path), "native_baseline_sha256": sha(DATA / "natural-baseline/results.jsonl"),
    "app_build_sha256": sha(APP.parent / "build.json"),
    "sdk_source": str((ROOT.parent / "stream-failure-20261107/native-build/checkouts/mlx-audio-swift/Sources/MLXAudioSTT/Models/Qwen3ASR/Qwen3ASR.swift").resolve()),
    "sdk_padding": "splitAudioIntoChunks lines 179-188 pads inputs shorter than one second only at the tail; generateManaged calls that splitter before preprocessing",
    "arms": ["Original unmodified WAV, native internal tail padding", "Explicit tail padding to 16000 frames, requiring prior original text/language/model parity", "Explicit leading padding to 16000 frames, only after both control arms qualify"],
    "fixed_variables": ["Every original PCM sample, amplitude and sample order", "16000 effective samples for all model calls", "Same accepted Helper/model weights/auto language/empty context/greedy parameters", "One serial offline native instance"],
    "execution": {"port": 18902, "request_timeout_seconds": 150, "whole_timeout_seconds": 600,
                  "retries": 0, "serial": True, "maximum_owned_model_processes": 1,
                  "order": "Seven originals in manifest order, seven explicit tails, seven explicit heads",
                  "language": "auto", "helper_sha256": BUILD["files"]["Contents/Helpers/CyberAudioService"]["sha256"],
                  "stop_if_production_busy": True},
    "qualification": {"baseline_gate": "Every original and explicit-tail text/language/no_speech/model/repository/revision must equal the frozen natural baseline; validate expected input durations separately; abort on mismatch",
                      "problem_gate": "Leading arm must match the reference under the frozen author mixed-token scorer",
                      "control_gate": "No leading-control mixed edit count may increase and no new empty control may appear",
                      "action": "This pilot never promotes a default, reruns a failed arm, adds cases or searches padding durations; any wider qualification needs a separate prospective protocol"},
    "cases": cases,
    "limits": ["Seven selected recordings are causal diagnostics, not general quality acceptance", "Reference text is not human listening or intelligibility acceptance", "Original strict M4, content, physical, public-download and distribution gates remain open"],
    "data_license": "CC-BY-SA-4.0", "data_attribution": "ASCEND, Lovenia et al., LREC 2022, HKUST CAiRE",
    "driver_files_sha256": {name: sha(ROOT / name) for name in ["prepare.py", "run.py", "analyze.py", "transform-verification.json"]},
}
protocol["sdk_source_sha256"] = sha(protocol["sdk_source"])
put("protocol.json", protocol)
print(json.dumps({"status": "protocol_frozen_no_inference", "selected": 7, "expected_requests": 21,
                  "eligible_short_rows": len(eligible), "eligible_languages": protocol["eligible_language_counts"],
                  "protocol_sha256": sha(ROOT / "protocol.json"), "verified_ffmpeg_transforms": len(transforms)}, indent=2))
