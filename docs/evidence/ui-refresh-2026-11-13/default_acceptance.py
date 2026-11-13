import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import uuid
import wave

project = Path.cwd()
spec = importlib.util.spec_from_file_location("variants", project / "scripts/validate_model_variants.py")
variants = importlib.util.module_from_spec(spec)
spec.loader.exec_module(variants)
out = project / ".context/design-refresh"
root = out / "default-runtime"
app = project / "dist/CyberAudio.app/Contents"
port = 19888
with socket.socket() as listener:
    listener.bind(("127.0.0.1", port))
models = json.loads((project / "Native/Sources/CyberAudioCore/ModelCatalog.lock.json").read_text())["models"]
selected = [next(model for model in models if model["id"] == name) for name in ["qwen3-asr-1.7b", "qwen3-tts-1.7b"]]
directories = {model["component"]: variants.wait_files(model, project / ".context/model-acceptance/models", 2) for model in selected}
subprocess.run([str(app / "Helpers/CyberAudioAssets"), "prepare", "--root", str(root), "--port", str(port)], check=True, stdout=subprocess.DEVNULL)
config_path = root / "config/service.json"
config = json.loads(config_path.read_text())
config.update(asr_dir=str(directories["asr"]), tts_dir=str(directories["tts"]),
              asr_model_id=selected[0]["id"], tts_model_id=selected[1]["id"])
config_path.write_text(json.dumps(config))
token = (root / "config/api-token").read_text().strip()
instance = uuid.uuid4().hex
before = json.loads(variants.http(8765, "/health")[0])["instance"]
process = subprocess.Popen(["/usr/bin/sandbox-exec", "-f", str(project / "packaging/offline.sb"),
                            str(app / "Helpers/CyberAudioService"), "--root", str(root),
                            "--parent-pid", str(os.getpid()), "--instance", instance,
                            "--metallib", str(app / "Resources/mlx.metallib")],
                           stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
summary = {"host": "codehubmac-mini", "scope": "new packaged default pair", "cases": []}
try:
    deadline = time.monotonic() + 180
    while True:
        assert process.poll() is None, "Owned service exited during loading"
        assert time.monotonic() < deadline, "Owned service readiness timed out"
        try:
            ready = json.loads(variants.http(port, "/ready")[0])
            assert ready.get("status") != "failed", ready
            if ready.get("instance") == instance and ready.get("status") == "ready":
                break
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.2)
    summary["startup"] = ready["startup"]
    summary["models"] = json.loads(variants.http(port, "/v1/models", token)[0])["data"]
    for language, stream, text in [("zh", False, "你好，这是本地语音模型的测试。"), ("en", True, "Hello, this is a local speech model test.")]:
        audio, headers = variants.http(port, "/v1/audio/speech", token, json.dumps({
            "input": text, "voice": "serena", "language": language,
            "instructions": "自然清晰地朗读。", "stream": stream, "response_format": "pcm" if stream else "wav"
        }).encode())
        assert next(v for k, v in headers.items() if k.lower() == "x-model") == "qwen3-tts-1.7b"
        path = root / (language + ".wav")
        if stream:
            assert len(audio) > 0 and len(audio) % 2 == 0
            with wave.open(str(path), "wb") as file:
                file.setnchannels(1); file.setsampwidth(2); file.setframerate(24000); file.writeframes(audio)
            request_id = next(v for k, v in headers.items() if k.lower() == "x-request-id")
            assert json.loads(variants.http(port, "/v1/requests/" + request_id, token)[0])["status"] == "completed"
        else:
            path.write_bytes(audio)
        with wave.open(str(path)) as file:
            assert file.getnchannels() == 1 and file.getframerate() == 24000 and file.getnframes() > 0
            duration = file.getnframes() / 24000
        result = variants.transcription(port, token, "qwen3-asr-1.7b", path.read_bytes(), language)
        assert result["text"].strip() and not result["no_speech"] and result["model"] == "qwen3-asr-1.7b"
        summary["cases"].append({"language": language, "stream": stream, "duration": duration, "transcription": result["text"], "passed": True})
    summary["passed"] = True
finally:
    process.stdin.close()
    try:
        process.wait(timeout=12)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
    summary["production_service_instance_unchanged"] = json.loads(variants.http(8765, "/health")[0])["instance"] == before
    (out / "default-runtime.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
assert summary["production_service_instance_unchanged"]
print(json.dumps(summary, ensure_ascii=False, indent=2))
