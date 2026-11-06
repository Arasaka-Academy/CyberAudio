"""Content diagnostics after the fixed-seed TTS process has actually exited."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import wave

base = Path(__file__).resolve().parent
project = base.parents[2]
root = base/"m1-tts-temperature-roundtrip"
assert not root.exists()
seeded = base/"m1-seeded-tts-probe"
receipt = json.loads((base/"seeded-tts-result.json").read_text())
assert receipt["status"] == "completed" and receipt["exit_code"] == 0
seeds = json.loads((seeded/"report.json").read_text())
assert seeds["tokens_repeat_identically"]
app = project/"dist/CyberAudio.app"
helper = app/"Contents/Helpers/CyberAudioService"
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert sha(helper) == "cc556b65e263e16218db4315ed8585dccaede7b5f539be7bbd68a4b04e841a89"
port = 18879
with socket.socket() as probe:
    assert probe.connect_ex(("127.0.0.1", port)) != 0
process = None
token = ""
report = {"status": "running", "started": datetime.now(timezone.utc).isoformat(),
          "scope": "Three private TTS temperature/recovery outputs through the unchanged actual ASR Helper; no listening acceptance",
          "helper_sha256": sha(helper), "port": port, "results": []}
began = time.monotonic()

def save():
    path = root/"reports/asr-followup.json"
    report["elapsed_seconds"] = time.monotonic()-began
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    temporary.replace(path)

def fetch(path, body=None, content_type=None, production=False):
    headers = {} if production else {"Authorization": "Bearer "+token}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(f"http://127.0.0.1:{8765 if production else port}"+path, data=body, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=150)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)

def production_snapshot():
    state = fetch("/health", production=True)[1]
    data = Path.home()/"Library/Application Support/CyberAudio"
    return {"instance": state["instance"], "config_sha256": sha(data/"config/service.json"),
            "token_sha256": sha(data/"config/api-token")}

try:
    report["production_before"] = production_snapshot()
    subprocess.run([str(helper.with_name("CyberAudioAssets")), "prepare", "--root", str(root), "--port", str(port)],
                   check=True, stdout=subprocess.DEVNULL, timeout=30)
    (root/"audio").mkdir()
    configuration = json.loads((root/"config/service.json").read_text())
    configuration.update(asr_dir=str((project/".context/robustness/linked-models/asr").resolve()),
                         tts_dir=str((project/".context/robustness/linked-models/tts").resolve()), ffmpeg="/opt/homebrew/bin/ffmpeg")
    (root/"config/service.json").write_text(json.dumps(configuration))
    token = (root/"config/api-token").read_text().strip()
    preserved = {name: sha(root/"config"/name) for name in ["service.json", "api-token"]}
    assert json.loads((base/"traced-tts-result.json").read_text())["exit_code"] == 0
    traced = base/"m1-traced-tts-probe"
    results = json.loads((traced/"report.json").read_text())["results"]
    manifest = []
    for row in results:
        if row["label"] not in ["temperature-09", "temperature-05", "recovery-seed-31"]:
            continue
        assert row["eos_seen"] and row["audio_seconds"] <= 60
        raw = traced/(row["label"]+".pcm")
        assert sha(raw) == row["pcm_sha256"]
        path = root/"audio"/(row["label"]+".wav")
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(1); writer.setsampwidth(2); writer.setframerate(24000); writer.writeframes(raw.read_bytes())
        manifest.append({"id": row["label"], "audio": str(path), "audio_sha256": sha(path),
            "language": "zh", "source_seed": row["seed"], "temperature": row["temperature"],
            "source_pcm_sha256": sha(raw), "expected_source_text": seeds["text"], "kind": "tts_retranscription"})
    (root/"corpus.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
    instance = uuid.uuid4().hex
    process = subprocess.Popen(["/usr/bin/sandbox-exec", "-f", str(app/"Contents/Resources/offline.sb"),
        str(helper), "--root", str(root), "--parent-pid", str(os.getpid()), "--instance", instance,
        "--metallib", str(app/"Contents/Resources/mlx.metallib")], stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    until = time.monotonic()+180
    while True:
        assert process.poll() is None and time.monotonic() < until
        try:
            status, ready = fetch("/ready")
        except OSError:
            time.sleep(.2); continue
        assert ready["status"] != "failed"
        if status == 200 and ready["status"] == "ready":
            assert ready["instance"] == instance
            report["startup"] = ready
            break
        time.sleep(.2)
    for row in manifest:
        assert time.monotonic()-began < 600
        path = Path(row["audio"])
        assert sha(path) == row["audio_sha256"]
        boundary = "diagnostic-"+uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\n{row["language"]}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n\r\n').encode()
        body += path.read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
        status, result = fetch("/v1/audio/transcriptions", body, "multipart/form-data; boundary="+boundary)
        report["results"].append({"id": row["id"], "http_status": status, "response": result})
        save()
        assert status == 200, result
        print(row["id"], result["text"], flush=True)
    assert not list((root/"tmp").glob("request-*"))
    process.stdin.close(); process.wait(timeout=12)
    assert process.returncode == 0
    report["owned_helper_exit_code"] = process.returncode
    report["temporary_request_cleanup"] = True
    report["production_after"] = production_snapshot()
    assert report["production_after"] == report["production_before"]
    assert preserved == {name: sha(root/"config"/name) for name in preserved}
    report["production_and_config_preserved"] = True
    report["status"] = "diagnostic_completed"
except Exception as error:
    report["status"] = "failed"; report["error"] = str(error)
    raise
finally:
    if process is not None and process.poll() is None:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=12)
            report["forced_cleanup"] = True
    report["finished"] = datetime.now(timezone.utc).isoformat()
    if (root/"reports").exists():
        save()
