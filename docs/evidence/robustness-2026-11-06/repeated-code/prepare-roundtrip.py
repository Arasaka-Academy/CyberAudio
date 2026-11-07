"""Derive every complete TTS output into the prospectively fixed ASR routes."""
from pathlib import Path
from datetime import datetime, timezone
import ast
import hashlib
import json
import subprocess
import wave

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
PLAN = json.loads((ROOT / "protocol.json").read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


runner = json.loads((ROOT / "runner.json").read_text())
assert runner["status"] == "generations_complete_not_content_accepted"
assert runner["necessary_generation_gate_passed"], "No ASR expansion after a failed necessary generation gate"
assert runner["production_preserved"] and runner["source_preserved"] and runner["frozen_preserved"]
audio = ROOT / "roundtrip-audio"
audio.mkdir(mode=0o700, exist_ok=False)
cases = []
transforms = []
for arm in ["baseline", "candidate"]:
    report = json.loads((ROOT / arm / "report.json").read_text())
    assert [row["case"] for row in report["results"]] == PLAN["cases"]
    for result in report["results"]:
        path = ROOT / arm / result["pcm_file"]
        assert sha(path) == result["pcm_sha256"]
        pcm = path.read_bytes()
        chunks = [pcm[start:start + 1152000] for start in range(0, len(pcm), 1152000)]
        assert b"".join(chunks) == pcm
        for index, chunk in enumerate(chunks):
            name = f"{arm}-{result['id']}-p{index}"
            wav = audio / (name + ".wav")
            assert len(chunk) % 6 == 0
            with wav.open("xb") as stream:
                with wave.open(stream, "wb") as sound:
                    sound.setparams((1, 2, 24000, len(chunk) // 2, "NONE", "not compressed"))
                    sound.writeframes(chunk)
            decoded = subprocess.run(["/opt/homebrew/bin/ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file,pipe",
                                      "-format_whitelist", "wav", "-i", str(wav), "-map", "0:a:0", "-vn", "-t", "60.1", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"],
                                     capture_output=True, check=True, timeout=15)
            assert len(decoded.stdout) == len(chunk) * 2 // 3
            transforms.append({"id": name, "pcm24_sha256": hashlib.sha256(chunk).hexdigest(),
                               "pcm16_sha256": hashlib.sha256(decoded.stdout).hexdigest(),
                               "source_frames": len(chunk) // 2, "normalized_frames": len(decoded.stdout) // 2})
            for route, language in [("auto", "auto"), ("producer", {"chinese": "zh", "english": "en"}[result["case"]["language"]])]:
                cases.append({"id": name + "-" + route, "source_id": result["id"], "arm": arm, "part": index,
                              "parts": len(chunks), "route": route, "request_language": language,
                              "audio": str(wav.resolve()), "audio_sha256": sha(wav),
                              "frames": len(decoded.stdout) // 2, "reference": result["case"]["text"],
                              "pcm16_sha256": hashlib.sha256(decoded.stdout).hexdigest()})
with (ROOT / "roundtrip-manifest.json").open("x") as stream:
    json.dump({"created_at": datetime.now(timezone.utc).isoformat(), "protocol_sha256": sha(ROOT / "protocol.json"),
               "rule": "All12 complete PCM outputs, deterministic <=24s chunks, each auto and original producer language, no selection", "expected_requests": len(cases), "cases": cases}, stream, ensure_ascii=False, indent=2); stream.write("\n")
with (ROOT / "roundtrip-transform.json").open("x") as stream:
    json.dump({"all_complete_pcm_preserved": True, "cases": transforms}, stream, indent=2); stream.write("\n")
source = (ROOT.parent / "natural-level-20261107/run.py").read_text()


def replace_once(before, after):
    global source
    assert source.count(before) == 1, before
    source = source.replace(before, after, 1)


replace_once('"""Run the fixed seven-recording input-level causal protocol once."""', '"""Read back every fixed complete TTS artifact after the necessary generation gate."""')
replace_once('APP = PROJECT / ".context/robustness/stream-failure-20261107/final-bytes-stream-failure/CyberAudio.app"', 'APP = Path(STUDY_PROTOCOL["app"])')
replace_once('ROOT = STUDY / "native-http"', 'ROOT = STUDY / "roundtrip-http"')
replace_once('    manifest = STUDY_PROTOCOL["cases"]\n    assert len(manifest) == STUDY_PROTOCOL["expected_requests"]',
             '    derived = json.loads((STUDY / "roundtrip-manifest.json").read_text())\n'
             '    assert derived["protocol_sha256"] == sha(STUDY / "protocol.json")\n'
             '    manifest = derived["cases"]\n'
             '    assert len(manifest) == derived["expected_requests"]')
replace_once('["protocol.json", "run.py", "transform-verification.json"]', '["protocol.json", "run-roundtrip.py", "prepare-roundtrip.py", "roundtrip-manifest.json", "roundtrip-transform.json"]')
replace_once('assert sha(HELPER) == PROTOCOL["helper_sha256"] == BUILD["files"]["Contents/Helpers/CyberAudioService"]["sha256"]',
             'assert sha(HELPER) == STUDY_PROTOCOL["execution"]["helper_sha256"] == BUILD["files"]["Contents/Helpers/CyberAudioService"]["sha256"]')
replace_once('                audio = BASE / "ascend-private" / row["audio"]', '                audio = Path(row["audio"])')
replace_once('name="language"\\r\\n\\r\\nauto\\r\\n', 'name="language"\\r\\n\\r\\n{row["request_language"]}\\r\\n')
ast.parse(source)
with (ROOT / "run-roundtrip.py").open("x") as stream:
    stream.write(source)
with (ROOT / "roundtrip-implementation.json").open("x") as stream:
    json.dump({"created_at": datetime.now(timezone.utc).isoformat(), "protocol_sha256": sha(ROOT / "protocol.json"),
               "necessary_generation_gate_passed": True, "expected_requests": len(cases),
               "files_sha256": {name: sha(ROOT / name) for name in ["prepare-roundtrip.py", "run-roundtrip.py", "roundtrip-manifest.json", "roundtrip-transform.json"]}}, stream, indent=2); stream.write("\n")
print(json.dumps({"status": "all_fixed_tts_artifacts_prepared_for_roundtrip", "outputs": 12, "requests": len(cases), "no_asr_run_yet": True}))
