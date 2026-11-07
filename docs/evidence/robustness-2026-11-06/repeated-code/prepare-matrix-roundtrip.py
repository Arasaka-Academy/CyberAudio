"""Derive both ASR routes for every complete original/candidate PCM artifact."""
from pathlib import Path
from datetime import datetime, timezone
import ast
import hashlib
import json
import subprocess
import wave

ROOT = Path(__file__).resolve().parent


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


plan = json.loads((ROOT / "matrix-plan.json").read_text())
runner = json.loads((ROOT / "matrix-runner.json").read_text())
assert runner["status"] == "all144_generated_not_content_accepted" and runner["owned_model_exit_code"] == 0
assert runner["production_preserved"] and runner["source_preserved"]
generated = json.loads((ROOT / "matrix-candidate/report.json").read_text())["results"]
assert [row["id"] for row in generated] == [row["id"] for row in plan["cases"]]
sources = []
for row in plan["baselines"]:
    sources.append(dict(row, arm="baseline", pcm_file=row["pcm_source"]))
for case, row in zip(plan["cases"], generated):
    assert all(row[key] == case[key] for key in case)
    sources.append(dict(row, case=case, arm="candidate", pcm_file=str(ROOT / "matrix-candidate" / (row["id"] + ".pcm"))))
audio = ROOT / "matrix-roundtrip-audio"
audio.mkdir(mode=0o700, exist_ok=False)
cases, transforms = [], []
for row in sources:
    assert sha(row["pcm_file"]) == row["pcm_sha256"]
    pcm = Path(row["pcm_file"]).read_bytes()
    chunks = [pcm[i:i+1152000] for i in range(0, len(pcm), 1152000)]
    assert chunks and b"".join(chunks) == pcm
    for index, chunk in enumerate(chunks):
        name = f"{row['arm']}-{row['id']}-p{index}"
        wav = audio / (name + ".wav")
        assert len(chunk) % 6 == 0
        with wav.open("xb") as stream:
            with wave.open(stream, "wb") as sound:
                sound.setparams((1, 2, 24000, len(chunk)//2, "NONE", "not compressed")); sound.writeframes(chunk)
        decoded = subprocess.run(["/opt/homebrew/bin/ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file,pipe",
                                  "-format_whitelist", "wav", "-i", str(wav), "-map", "0:a:0", "-vn", "-t", "60.1", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"], capture_output=True, check=True, timeout=15)
        assert len(decoded.stdout) == len(chunk)*2//3
        transforms.append({"id": name, "pcm24_sha256": hashlib.sha256(chunk).hexdigest(),
                           "pcm16_sha256": hashlib.sha256(decoded.stdout).hexdigest(), "source_frames": len(chunk)//2,
                           "normalized_frames": len(decoded.stdout)//2})
        for route, language in [("auto", "auto"), ("producer", {"chinese":"zh", "english":"en"}[row["case"]["language"]])]:
            cases.append({"id": name+"-"+route, "source_id": row["id"], "arm": row["arm"], "part": index,
                          "parts": len(chunks), "route": route, "request_language": language,
                          "audio": str(wav.resolve()), "audio_sha256": sha(wav), "frames": len(decoded.stdout)//2,
                          "reference": row["case"]["text"], "pcm16_sha256": hashlib.sha256(decoded.stdout).hexdigest()})
for filename, value in [("matrix-roundtrip-manifest.json", {"created_at": datetime.now(timezone.utc).isoformat(),
                        "protocol_sha256": sha(ROOT / "matrix-plan.json"), "outputs": 288, "expected_requests": len(cases), "cases": cases}),
                       ("matrix-roundtrip-transform.json", {"all_complete_pcm_preserved": True, "cases": transforms})]:
    with (ROOT / filename).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2); stream.write("\n")
source = (ROOT / "run-roundtrip.py").read_text()
for old, new in [('"roundtrip-http"', '"matrix-roundtrip-http"'), ('"protocol.json"', '"matrix-plan.json"'),
                 ('"run-roundtrip.py"', '"run-matrix-roundtrip.py"'), ('"prepare-roundtrip.py"', '"prepare-matrix-roundtrip.py"'),
                 ('"roundtrip-manifest.json"', '"matrix-roundtrip-manifest.json"'), ('"roundtrip-transform.json"', '"matrix-roundtrip-transform.json"')]:
    assert old in source, old
    source = source.replace(old, new)
ast.parse(source)
with (ROOT / "run-matrix-roundtrip.py").open("x") as stream:
    stream.write(source)
with (ROOT / "matrix-roundtrip-implementation.json").open("x") as stream:
    json.dump({"outputs": 288, "expected_requests": len(cases), "protocol_sha256": sha(ROOT / "matrix-plan.json"),
               "source_runner_sha256": sha(ROOT / "run-roundtrip.py"), "files_sha256": {name:sha(ROOT/name) for name in
                ["prepare-matrix-roundtrip.py", "run-matrix-roundtrip.py", "matrix-roundtrip-manifest.json", "matrix-roundtrip-transform.json"]}}, stream, indent=2); stream.write("\n")
print(json.dumps({"status": "all288_complete_outputs_prepared", "expected_asr_requests": len(cases), "routes": 2, "no_asr_started": True}))
