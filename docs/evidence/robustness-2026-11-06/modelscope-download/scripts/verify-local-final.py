"""Independently inspect complete local receipts and original protected inputs."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import socket
import subprocess
import urllib.request
import wave

base = Path(__file__).resolve().parent
project = base.parents[2]


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(path):
    return json.loads(path.read_text())


build = load(base / "current-source/build.private.json")
receipt = load(base / "current-source/local-result.private.json")
native = load(base / "current-source/data-local/reports/native-service.json")
baseline = load(project / ".context/robustness/stream-failure-20261107/final-bytes-stream-failure/data-full/reports/native-service.json")
assert receipt["status"] == native["status"] == "passed" and receipt["exit_code"] == 0
keys = ["id", "language", "audio_sha256", "reference"]
rows, old = native["asr_corpus"], baseline["asr_corpus"]
assert len(rows) == len(old) == 100
assert [{k: row[k] for k in keys} for row in rows] == [{k: row[k] for k in keys} for row in old]
assert all(row["result"]["text"] == prior["result"]["text"] for row, prior in zip(rows, old))
corpus = project / ".context/robustness/m4-acceptance-20261106/corpus"
assert all(sha(corpus / row["audio"]) == row["audio_sha256"] for row in load(corpus / "corpus.json"))
voices = []
for row in native["tts_voices"]:
    path = base / "current-source/data-local/reports" / (row["voice"] + ".wav")
    with wave.open(str(path), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (24000, 1, 2)
        frames = audio.getnframes()
        assert frames > 0 and len(audio.readframes(frames)) == frames * 2
        assert abs(frames / 24000 - row["duration"]) < 1e-9
    voices.append({"voice": row["voice"], "frames": frames, "sha256": sha(path)})
assert len(voices) == 9
assert all(sha(project / name) == digest for name, digest in build["source_sha256"].items())
assert all(sha(project / name) == digest for name, digest in build["frozen_sha256"].items())
before = load(base / "source-before.private.json")["files"]
changed = sorted(name for name, digest in before.items() if sha(project / name) != digest)
expected = sorted(["Native/Sources/CyberAudioAssets/Main.swift", "Native/Sources/CyberAudioCore/Assets.swift",
                   "Native/Tests/CyberAudioCoreTests/AssetDownloadTests.swift", "Native/Tests/CyberAudioCoreTests/Runner.swift",
                   "Native/Tests/CyberAudioCoreTests/ServiceTests.swift", "Sources/CyberAudio/PreparationController.swift",
                   "Sources/CyberAudio/PreparationView.swift", "tests/PreparationTests.swift", "tests/WorkspaceLayoutTests.swift"])
assert changed == expected
app = base / "current-source/CyberAudio.app"
actual = {str(path.relative_to(app)) for path in app.rglob("*") if path.is_symlink() or path.is_file()}
assert actual == set(build["app_files"])
for name, value in build["app_files"].items():
    path = app / name
    if value["type"] == "symlink":
        assert path.is_symlink() and os.readlink(path) == value["target"]
    else:
        assert not path.is_symlink() and sha(path) == value["sha256"]
assert native["helper_sha256"] == build["app_files"]["Contents/Helpers/CyberAudioService"]["sha256"]
assert sha(base / "current-source/verify-native") == build["driver_sha256"] == receipt["driver_sha256"]
assert sha(project / "scripts/verify_native.swift") == build["driver_source_sha256"]
metadata = load(project / ".context/robustness/natural-reference-20261107-complete/protected-metadata-before.private.json")
assert all([Path(path).stat().st_size, Path(path).stat().st_mtime_ns] == expected for path, expected in metadata.items())
shared = load(project / ".context/robustness/repeated-code-20261107/build/build.json")["protected_inputs_sha256"]
assert all(sha(path) == digest for path, digest in shared.items())
assert all([Path(path).stat().st_size, Path(path).stat().st_mtime_ns] == expected
           for path, expected in receipt["model_metadata_before"].items())
locked = load(project / "Native/Sources/CyberAudioCore/Models.lock.json")["files"]
for item in locked:
    path = project / ".context/robustness/linked-models" / item["path"]
    assert path.stat().st_size == item["size"] and sha(path) == item["sha256"]
production = Path.home() / "Library/Application Support/CyberAudio"
with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
    ready = json.load(response)
assert {"instance": ready["instance"], "status": ready["status"], "config_sha256": sha(production / "config/service.json"),
        "token_sha256": sha(production / "config/api-token")} == receipt["production_before"] == receipt["production_after"]
with socket.socket() as stream:
    assert stream.connect_ex(("127.0.0.1", 18905)) != 0
processes = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True)
assert not any(str(base / "current-source") in line and ("CyberAudio.app/Contents/MacOS" in line or "CyberAudioService --root" in line)
               for line in processes.splitlines())
assert not list((base / "current-source/data-local/tmp").glob("request-*"))
summary = {"status": "independently_verified", "verified_at": datetime.now(timezone.utc).isoformat(),
           "corpus_files_verified": 100, "same_M1_raw_texts": 100, "corpus_identity_order_preserved": True,
           "voices": voices, "compiled_source_hashes": len(build["source_sha256"]), "preexisting_source_files": len(before),
           "expected_feature_source_changes": changed, "unrelated_sources_preserved": True,
           "shared_build_inputs_verified": len(shared), "protected_metadata_verified": len(metadata),
           "frozen_artifacts_verified": len(build["frozen_sha256"]), "current_app_files_verified": len(actual),
           "model_files_verified": len(locked), "model_bytes_verified": sum(item["size"] for item in locked),
           "production_preserved": True, "model_metadata_preserved": True, "driver_preserved": True,
           "owned_port_closed": True, "owned_model_processes_absent": True, "temporary_request_directories": 0,
           "native_report_sha256": sha(base / "current-source/data-local/reports/native-service.json")}
with (base / "local-final-verification.json").open("x") as stream:
    stream.write(json.dumps(summary, indent=2) + "\n")
print(json.dumps({k: v for k, v in summary.items() if k not in ["voices", "expected_feature_source_changes"]}, indent=2))
