"""Read-only verification after the single complete M4 acceptance invocation."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import socket
import subprocess
import urllib.request
import wave

target = Path.home() / "Library/Caches/CyberAudio/RobustnessValidation/20261106-e2f34df-target1"
base = target / "model-route-20261107"
download = target / "fresh-download"
production = Path.home() / "Library/Application Support/CyberAudio"


def load(path):
    return json.loads(path.read_text())


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def app_matches(app, files):
    actual = {str(path.relative_to(app)) for path in app.rglob("*") if path.is_symlink() or path.is_file()}
    assert actual == set(files)
    for name, expected in files.items():
        path = app / name
        if expected["type"] == "symlink":
            assert path.is_symlink() and os.readlink(path) == expected["target"]
        else:
            assert not path.is_symlink() and sha(path) == expected["sha256"]
    return len(files)


downloaded = load(base / "full-download/result.private.json")
receipt = load(base / "fresh-acceptance/result.private.json")
native = load(base / "fresh-acceptance/data/reports/native-service.json")
manifest = load(base / "current-source/manifest.json")
configuration = load(download / "config/service.json")
assert receipt["status"] == native["status"] == "passed" and receipt["exit_code"] == 0
assert downloaded["status"] == "download_verified" and downloaded["download_exit_code"] == 0
assert sha(target / "Models.lock.json") == "668125b12cc2c1b469925e1eb60c79171c6f9623470622c619e4583971f4e816"
locked = load(target / "Models.lock.json")["files"]
verified = []
for item in locked:
    kind, relative = item["path"].split("/", 1)
    path = Path(configuration[kind + "_dir"]) / relative
    assert download.resolve() in path.resolve().parents and not path.is_symlink()
    assert path.stat().st_size == item["size"] and sha(path) == item["sha256"]
    verified.append(item)
for name, (size, mtime, digest) in downloaded["finals_before"].items():
    kind, relative = name.split("/", 1)
    path = Path(configuration[kind + "_dir"]) / relative
    assert [path.stat().st_size, path.stat().st_mtime_ns, sha(path)] == [size, mtime, digest]
with (Path(configuration["tts_dir"]) / "model.safetensors").open("rb") as stream:
    assert hashlib.sha256(stream.read(176 * 1024**2)).hexdigest() == downloaded["partial_sha256_before"]
assert not list((download / "tmp/downloads").glob("*.partial"))
assert downloaded["status_after"]["resumable_bytes"] == 0
assert all([Path(path).stat().st_size, Path(path).stat().st_mtime_ns] == expected
           for path, expected in receipt["model_metadata_before"].items())
app_count = app_matches(base / "current-source/CyberAudio.app", manifest["final_app_files"])
frozen_count = app_matches(target / "artifacts/CyberAudio.app", manifest["base_app_files"])
assert sha(base / "current-source/verify-native") == receipt["driver_sha256"] == manifest["driver_sha256"]
assert native["helper_sha256"] == manifest["final_app_files"]["Contents/Helpers/CyberAudioService"]["sha256"]
with urllib.request.urlopen("http://127.0.0.1:8765/ready", timeout=5) as response:
    ready = json.load(response)
observed = {"instance": ready["instance"], "status": ready["status"], "busy": ready["busy"],
            "config_sha256": sha(production / "config/service.json"), "token_sha256": sha(production / "config/api-token")}
assert observed == receipt["production_before"] == receipt["production_after"] == downloaded["production_after"]
assert sha(download / "config/service.json") == receipt["config_sha256_before"] == downloaded["config_sha256_before"]
assert sha(download / "config/api-token") == receipt["token_sha256_before"] == downloaded["token_sha256_before"]
with socket.socket() as stream:
    assert stream.connect_ex(("127.0.0.1", 18906)) != 0
processes = subprocess.check_output(["/bin/ps", "-axo", "pid=,command="], text=True)
assert not any(str(base) in line for line in processes.splitlines())
assert not list((base / "fresh-acceptance/data/tmp").glob("request-*"))
voices = []
for row in native["tts_voices"]:
    path = base / "fresh-acceptance/data/reports" / (row["voice"] + ".wav")
    with wave.open(str(path), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (24000, 1, 2)
        frames = audio.getnframes()
        assert frames > 0 and len(audio.readframes(frames)) == frames * 2
        assert abs(frames / 24000 - row["duration"]) < 1e-9
    voices.append({"voice": row["voice"], "frames": frames, "sha256": sha(path)})
assert len(voices) == 9
keys = ["id", "language", "audio_sha256", "reference"]
corpus = Path.home() / "Library/Caches/CyberAudio/CoreMLValidation/20261105/corpus"
rows = load(corpus / "corpus.json")
assert len(rows) == len(native["asr_corpus"]) == 100
assert [{k: row[k] for k in keys} for row in rows] == [{k: row[k] for k in keys} for row in native["asr_corpus"]]
assert all(sha(corpus / row["audio"]) == row["audio_sha256"] for row in rows)
baselines = [path for pattern in ["*/reports/native-service.json", "*/*/reports/native-service.json"]
             for path in target.glob(pattern) if "model-route-20261107" not in path.parts]
comparisons = []
for path in baselines:
    prior = load(path)
    if prior.get("status") != "passed" or len(prior.get("asr_corpus", [])) != 100:
        continue
    if [{k: row[k] for k in keys} for row in prior["asr_corpus"]] != [{k: row[k] for k in keys} for row in rows]:
        continue
    comparisons.append({"baseline": str(path.relative_to(target)), "report_sha256": sha(path),
                        "same_raw_texts": sum(a["result"]["text"] == b["result"]["text"]
                                              for a, b in zip(native["asr_corpus"], prior["asr_corpus"]))})
print(json.dumps({"status": "independently_verified", "verified_at": datetime.now(timezone.utc).isoformat(),
                  "locked_files": len(verified), "locked_bytes": sum(row["size"] for row in verified),
                  "verified_files": verified, "original14_finals_preserved": True, "original176MiB_prefix_preserved": True,
                  "partials_remaining": 0, "current_app_files": app_count, "frozen_app_files": frozen_count,
                  "driver_preserved": True, "helper_matches_app": True, "corpus_files_verified": len(rows),
                  "corpus_identity_order_preserved": True, "same_M4_comparisons": comparisons,
                  "voices": voices, "production_preserved": True, "download_config_token_preserved": True,
                  "model_metadata_preserved": True, "owned_port_closed": True, "owned_processes_absent": True,
                  "temporary_request_directories": 0, "native_report_sha256": sha(base / "fresh-acceptance/data/reports/native-service.json")}, indent=2))
