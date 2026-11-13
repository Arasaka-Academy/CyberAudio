#!/usr/bin/env python3
"""Run real native HTTP inference for every pinned variant on an isolated host."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import wave

PROJECT = Path(__file__).resolve().parents[1]


def http(port, path, token=None, payload=None, content_type="application/json"):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=payload)
    if token:
        request.add_header("Authorization", "Bearer " + token)
    if payload is not None:
        request.add_header("Content-Type", content_type)
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read(), dict(response.headers)


def transcription(port, token, model, audio, language):
    boundary = "CyberAudioAcceptance" + uuid.uuid4().hex
    body = bytearray()
    for name, value in [("model", model), ("language", language)]:
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode())
    body.extend(audio)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    result, _ = http(port, "/v1/audio/transcriptions", token, bytes(body), f"multipart/form-data; boundary={boundary}")
    return json.loads(result)


def wait_files(model, storage, timeout):
    directory = storage / model["repository"].split("/")[-1]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all((directory / file["path"].split("/", 1)[1]).is_file() and
               (directory / file["path"].split("/", 1)[1]).stat().st_size == file["size"] for file in model["files"]):
            return directory
        time.sleep(2)
    raise TimeoutError("The required variant has not finished downloading: " + model["id"])


def run(args):
    output = args.output.resolve()
    protected = Path.home() / "Library/Application Support/CyberAudio"
    if output == protected or protected in output.parents or output in protected.parents or args.port == 8765:
        raise ValueError("Use a dedicated output directory and a non-production port")
    marker = output / ".cyberaudio-model-acceptance"
    if output.exists() and not marker.exists():
        raise ValueError("Existing output directory lacks the isolated-acceptance marker")
    output.mkdir(parents=True, exist_ok=True)
    marker.touch()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", args.port))
    models = json.loads((PROJECT / "Native/Sources/CyberAudioCore/ModelCatalog.lock.json").read_text())["models"]
    default_asr = next(model for model in models if model["id"] == "qwen3-asr-1.7b")
    default_tts = next(model for model in models if model["id"] == "qwen3-tts-1.7b")
    pairs = [(default_asr, default_tts)]
    pairs += [(model, default_tts) if model["component"] == "asr" else (default_asr, model)
              for model in models if model["id"] not in {default_asr["id"], default_tts["id"]}]
    by_id = {model["id"]: model for model in models}
    pairs += [(by_id["qwen3-asr-0.6b-4bit"], by_id["qwen3-tts-0.6b-8bit"]),
              (by_id["qwen3-asr-1.7b-4bit"], by_id["qwen3-tts-1.7b-4bit"])]
    summary = {"host": socket.gethostname(), "cases": []}
    try:
        summary["production_instance_before"] = json.loads(http(8765, "/health")[0])["instance"]
    except Exception:
        summary["production_instance_before"] = None
    baseline = {}
    for index, (asr, tts) in enumerate(pairs):
        directories = {model["component"]: wait_files(model, args.models.resolve(), args.wait_seconds) for model in [asr, tts]}
        root = output / (f"{index:02d}-" + uuid.uuid4().hex)
        subprocess.run([str(args.assets), "prepare", "--root", str(root), "--port", str(args.port)], check=True, stdout=subprocess.DEVNULL)
        config_file = root / "config/service.json"
        config = json.loads(config_file.read_text())
        config.update(asr_dir=str(directories["asr"]), tts_dir=str(directories["tts"]),
                      asr_model_id=asr["id"], tts_model_id=tts["id"])
        config_file.write_text(json.dumps(config))
        token = (root / "config/api-token").read_text().strip()
        instance = uuid.uuid4().hex
        command = ["/usr/bin/sandbox-exec", "-f", str(PROJECT / "packaging/offline.sb"), str(args.service),
                   "--root", str(root), "--parent-pid", str(os.getpid()), "--instance", instance, "--metallib", str(args.metallib)]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        case = {"asr": asr["id"], "tts": tts["id"], "root": str(root)}
        try:
            deadline = time.monotonic() + 240
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Owned inference process exited during loading")
                if time.monotonic() > deadline:
                    raise TimeoutError("Owned inference process did not become ready")
                try:
                    ready = json.loads(http(args.port, "/ready")[0])
                    if ready.get("instance") == instance and ready.get("status") == "failed":
                        raise RuntimeError("Model loading failed; inspect " + str(root / "logs"))
                    if ready.get("instance") == instance and ready.get("status") == "ready":
                        break
                except (urllib.error.URLError, ConnectionError):
                    pass
                time.sleep(0.3)
            case["startup"] = ready.get("startup")
            catalog = json.loads(http(args.port, "/v1/models", token)[0])["data"]
            assert [model["id"] for model in catalog] == [asr["id"], tts["id"]]
            case["models"] = catalog
            voices = json.loads(http(args.port, "/v1/audio/voices", token)[0])
            assert len(voices["voices"]) == 9 and voices["supports_instructions"] == tts["supports_instructions"]
            try:
                http(args.port, "/v1/models")
                raise AssertionError("Unauthenticated model query was admitted")
            except urllib.error.HTTPError as error:
                assert error.code == 401
            case["transcriptions"] = {}
            for language, text in [("zh", "你好，这是本地语音模型的测试。"), ("en", "Hello, this is a local speech model test.")]:
                stream = language == "en"
                payload = {"model": tts["id"], "input": text, "voice": "serena", "language": language,
                           "instructions": "" if not tts["supports_instructions"] else "自然清晰地朗读。",
                           "stream": stream, "response_format": "pcm" if stream else "wav"}
                audio, headers = http(args.port, "/v1/audio/speech", token, json.dumps(payload).encode())
                assert next(value for key, value in headers.items() if key.lower() == "x-model") == tts["id"]
                path = root / (language + ".wav")
                if stream:
                    assert len(audio) > 0 and len(audio) % 2 == 0
                    with wave.open(str(path), "wb") as wav:
                        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000); wav.writeframes(audio)
                    request_id = next((value for key, value in headers.items() if key.lower() == "x-request-id"), None)
                    if request_id:
                        status = json.loads(http(args.port, "/v1/requests/" + request_id, token)[0])
                        assert status["status"] == "completed", status
                else:
                    path.write_bytes(audio)
                with wave.open(str(path)) as wav:
                    assert wav.getnchannels() == 1 and wav.getframerate() == 24000 and wav.getnframes() > 0
                if index == 0:
                    baseline[language] = path.read_bytes()
                result = transcription(args.port, token, asr["id"], baseline[language], language)
                assert result["model"] == asr["id"] and result["text"].strip() and not result["no_speech"], result
                case["transcriptions"][language] = result
            if not tts["supports_instructions"]:
                try:
                    http(args.port, "/v1/audio/speech", token, json.dumps({"input": "测试", "instructions": "开心"}).encode())
                    raise AssertionError("Unsupported instructions were admitted")
                except urllib.error.HTTPError as error:
                    assert error.code == 400 and json.load(error)["error"]["code"] == "unsupported_instructions"
            case["passed"] = True
            print("PASS", asr["id"], "+", tts["id"], flush=True)
        except Exception as error:
            case["passed"] = False
            case["failure"] = str(error)
            raise
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
            summary["cases"].append(case)
            (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    tested = {case[key] for case in summary["cases"] for key in ["asr", "tts"]}
    assert tested == {model["id"] for model in models}
    summary["production_instance_after"] = json.loads(http(8765, "/health")[0])["instance"]
    summary["production_instance_unchanged"] = summary["production_instance_after"] == summary["production_instance_before"]
    summary["all_eight_models_passed"] = True
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--assets", type=Path, default=PROJECT / ".context/native-build/arm64-apple-macosx/release/CyberAudioAssets")
    parser.add_argument("--service", type=Path, default=PROJECT / ".context/native-build/arm64-apple-macosx/release/CyberAudioService")
    parser.add_argument("--metallib", type=Path, default=Path("/Applications/CyberAudio.app/Contents/Resources/mlx.metallib"))
    parser.add_argument("--port", type=int, default=19765)
    parser.add_argument("--wait-seconds", type=int, default=3600)
    run(parser.parse_args())
