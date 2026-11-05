"""CyberAudio: fixed local STT/TTS models, owned by the native Mac app."""
from __future__ import annotations

import argparse
import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
import fcntl
import hmac
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import resource
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
import wave

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ASR_ID = "qwen3-asr-1.7b"
ASR_REPO = "mlx-community/Qwen3-ASR-1.7B-8bit"
ASR_REV = "a8379a2e2f9e313c9292cdf1af4055ab56d50d55"
TTS_ID = "qwen3-tts-1.7b"
TTS_REPO = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
TTS_REV = "41d3337e8b7f2843a75841595fc14e4b9a7a4b96"
VOICES = ["serena", "vivian", "uncle_fu", "ryan", "aiden", "ono_anna", "sohee", "eric", "dylan"]
MAX_UPLOAD = 20 * 1024 * 1024
MAX_TOKENS = 1200
LOG = logging.getLogger("cyberaudio")


@dataclass(frozen=True)
class Settings:
    asr_dir: Path
    tts_dir: Path
    token: str
    temp_dir: Path
    ffmpeg: str = "/opt/homebrew/bin/ffmpeg"
    instance: str = "test"
    timeout: float = 120


class AudioError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def error(status, code, message, **kwargs):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, **kwargs)


class UpdateRequestDrain:
    """Keep an accepted audio response live until ASGI finishes sending it."""
    def __init__(self, app, state, lock):
        self.app, self.state, self.lock = app, state, lock

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] not in (
            "/v1/audio/speech", "/v1/audio/transcriptions"
        ):
            return await self.app(scope, receive, send)
        with self.lock:
            paused = time.monotonic() < self.state.update_deadline
            if not paused:
                self.state.audio_responses += 1
        if paused:
            return await error(503, "updating", "正在准备更新，请稍后重试。", headers={"Retry-After": "2"})(scope, receive, send)
        try:
            return await self.app(scope, receive, send)
        finally:
            with self.lock:
                self.state.audio_responses -= 1


class RequestBoundary:
    """Authenticate and bound bodies before multipart/JSON parsing, including chunked bodies."""
    def __init__(self, app, token):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")]
            await send(message)

        path = scope["path"]
        if path.startswith(("/v1/", "/internal/")):
            auth = [value for key, value in scope["headers"] if key.lower() == b"authorization"]
            if len(auth) != 1 or not hmac.compare_digest(auth[0], b"Bearer " + self.token):
                return await error(401, "unauthorized", "令牌无效或缺失。", headers={"WWW-Authenticate": "Bearer"})(scope, receive, secured_send)
        limit = MAX_UPLOAD + 1024 * 1024 if path == "/v1/audio/transcriptions" else 16 * 1024
        if scope["method"] == "POST":
            try:
                length = int(dict(scope["headers"]).get(b"content-length", b"0"))
            except ValueError:
                return await error(400, "invalid_request", "请求长度无效。")(scope, receive, secured_send)
            if length < 0 or length > limit:
                return await error(413, "upload_too_large", "请求超过大小限制。")(scope, receive, secured_send)
            chunks, length = [], 0
            deadline = time.monotonic() + 30
            while True:
                try:
                    message = await asyncio.wait_for(receive(), max(0.001, deadline - time.monotonic()))
                except TimeoutError:
                    return await error(408, "upload_timeout", "上传超时。")(scope, receive, secured_send)
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                length += len(chunk)
                if length > limit:
                    return await error(413, "upload_too_large", "请求超过大小限制。")(scope, receive, secured_send)
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            body, sent = b"".join(chunks), False

            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            return await self.app(scope, replay, secured_send)
        return await self.app(scope, receive, secured_send)


def cleanup_stale(temp_dir):
    for entry in temp_dir.glob("request-*"):
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)


def decode(settings, source, destination):
    arguments = [settings.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                 "-protocol_whitelist", "file,pipe", "-format_whitelist", "wav,mp3,mov,matroska,webm,ogg,flac,aac,aiff",
                 "-i", str(source), "-map", "0:a:0", "-vn", "-t", "60.1", "-ar", "16000", "-ac", "1",
                 "-c:a", "pcm_s16le", "-y", str(destination)]
    try:
        result = subprocess.run(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=25)
    except subprocess.TimeoutExpired:
        raise AudioError(400, "decode_timeout", "音频解码超时。") from None
    if result.returncode:
        raise AudioError(400, "invalid_audio", "无法读取音频，请上传 WAV、MP3、M4A 或 WebM。")
    try:
        with wave.open(str(destination), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
            samples = audio.readframes(audio.getnframes())
    except (wave.Error, EOFError):
        raise AudioError(400, "invalid_audio", "音频数据无效。") from None
    if duration > 60:
        raise AudioError(413, "audio_too_long", "音频不能超过 60 秒。")
    if not samples:
        raise AudioError(400, "empty_audio", "音频为空。")
    return duration, not any(samples)


def wav_bytes(pcm):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(pcm)
    return buffer.getvalue()


class QwenEngine:
    """All MLX operations, loading included, remain on the same dedicated thread."""
    def __init__(self, settings):
        import mlx.core as mx
        import numpy as np
        from mlx_audio.stt import load as load_asr
        from mlx_audio.tts.utils import load_model as load_tts

        if not mx.metal.is_available():
            raise RuntimeError("Metal unavailable")
        self.mx, self.np = mx, np
        mx.set_default_device(mx.gpu)
        mx.set_cache_limit(512 * 1024 * 1024)
        for path, kind in [(settings.asr_dir, "qwen3_asr"), (settings.tts_dir, "qwen3_tts")]:
            config = json.loads((path / "config.json").read_text())
            if config.get("model_type") != kind or config.get("quantization", {}).get("bits") != 8:
                raise RuntimeError("Unexpected model identity")
        config = json.loads((settings.tts_dir / "config.json").read_text())
        if config.get("tts_model_type") != "custom_voice" or config.get("tts_model_size") != "1b7":
            raise RuntimeError("Unexpected TTS variant")
        begin = time.monotonic()
        self.asr = load_asr(str(settings.asr_dir))
        self.tts = load_tts(str(settings.tts_dir))
        loaded = time.monotonic()
        self.asr.generate(np.zeros(16000, dtype=np.float32), language="Chinese", max_tokens=16, temperature=0.0, batch_size=1)
        # Warm caches through the same generation path used by requests.
        for _ in self.tts.generate_custom_voice(text="你好。", speaker="serena", language="chinese", max_tokens=48, stream=True, streaming_interval=0.32, verbose=False):
            pass
        self.metrics = {"device": "metal", "load_ms": round((loaded - begin) * 1000, 1),
                        "warmup_ms": round((time.monotonic() - loaded) * 1000, 1), **self.memory()}
        LOG.info("models_ready %s", json.dumps(self.metrics))

    def memory(self):
        return {"metal_peak_bytes": self.mx.get_peak_memory(), "metal_active_bytes": self.mx.get_active_memory(),
                "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}

    def transcribe(self, audio, language):
        result = self.asr.generate(str(audio), language=language, max_tokens=1024, temperature=0.0, batch_size=1)
        detected = getattr(result, "language", language)
        if isinstance(detected, list):
            detected = detected[0] if detected else None
        return {"text": result.text.strip(), "language": {"Chinese": "zh", "English": "en"}.get(detected, detected), **self.memory()}

    def synthesize(self, speech, cancelled, deadline):
        tokens = 0
        generator = self.tts.generate_custom_voice(text=speech.input.strip(), speaker=speech.voice,
            language=speech.language, instruct=speech.instructions or None, max_tokens=MAX_TOKENS,
            temperature=0.7, stream=True, streaming_interval=0.32, verbose=False)
        try:
            for result in generator:
                if cancelled.is_set():
                    raise AudioError(499, "cancelled", "请求已取消。")
                if time.monotonic() > deadline:
                    raise AudioError(504, "inference_timeout", "生成超时，请缩短文本。")
                tokens += result.token_count
                if tokens >= MAX_TOKENS:
                    raise AudioError(422, "generation_limit", "生成达到长度上限，请缩短文本。")
                audio = self.np.asarray(result.audio, dtype=self.np.float32).reshape(-1)
                if result.sample_rate != 24000 or not self.np.isfinite(audio).all():
                    raise RuntimeError("Invalid generated audio")
                pcm = (self.np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
                if pcm:
                    yield pcm
        finally:
            generator.close()
            self.tts.speech_tokenizer.decoder.reset_streaming_state()
            self.mx.clear_cache()


class Speech(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str = TTS_ID
    input: str = Field(min_length=1, max_length=500)
    voice: str = "serena"
    language: str = "auto"
    instructions: str = Field(default="", max_length=200)
    response_format: str = "wav"
    stream: bool = False


def create_app(settings, engine_factory=QwenEngine):
    settings.temp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(settings.temp_dir, 0o700)
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cyberaudio-model")
    lock = threading.Lock()
    records = OrderedDict()

    @asynccontextmanager
    async def lifespan(app):
        def initialize():
            try:
                app.state.engine = engine_factory(settings)
                app.state.status = "ready"
            except Exception as exc:
                LOG.error("initialization_failed type=%s", type(exc).__name__)
                app.state.status = "failed"
        worker.submit(initialize)
        yield
        if app.state.cancelled:
            app.state.cancelled.set()
        await asyncio.to_thread(worker.shutdown, wait=True, cancel_futures=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.status, app.state.busy, app.state.engine, app.state.cancelled = "loading", False, None, None
    app.state.audio_responses = 0
    app.state.update_deadline = 0.0
    app.state.update_lease = None
    app.add_middleware(UpdateRequestDrain, state=app.state, lock=lock)
    app.add_middleware(RequestBoundary, token=settings.token)  # Authentication remains outermost.
    cancelled_updates = OrderedDict()

    def updating():
        # A renewable lease prevents an abandoned update from leaving the API paused.
        return time.monotonic() < app.state.update_deadline

    def update_state():
        return {"instance": settings.instance, "busy": app.state.busy or app.state.audio_responses > 0, "updating": updating()}

    def check_update_owner(request):
        if request.headers.get("X-CyberAudio-Instance") != settings.instance:
            raise AudioError(409, "wrong_instance", "更新请求不属于当前引擎。")
        lease = request.headers.get("X-CyberAudio-Update", "")
        if not lease or len(lease) > 64:
            raise AudioError(400, "invalid_update", "更新标识无效。")
        return lease

    @app.post("/internal/update/prepare")
    async def prepare_update(request: Request):
        lease = check_update_owner(request)
        with lock:
            if lease in cancelled_updates:
                raise AudioError(409, "update_cancelled", "这次更新等待已取消。")
            if updating() and app.state.update_lease != lease:
                raise AudioError(409, "update_in_progress", "已有更新正在准备。")
            app.state.update_lease = lease
            app.state.update_deadline = time.monotonic() + 30
            return update_state()

    @app.post("/internal/update/cancel")
    async def cancel_update(request: Request):
        lease = check_update_owner(request)
        with lock:
            cancelled_updates[lease] = True
            while len(cancelled_updates) > 64:
                cancelled_updates.popitem(last=False)
            if app.state.update_lease == lease:
                app.state.update_deadline = 0.0
            return update_state()

    @app.get("/health")
    async def health():
        return {"status": "ok", "application": "CyberAudio", "instance": settings.instance}

    @app.get("/ready")
    async def ready():
        with lock:
            state, admission = app.state.status, update_state()
        return JSONResponse({"status": state, **admission, "model": ASR_ID,
            "application": "CyberAudio", "instance": settings.instance,
            "models": {"stt": state, "tts": state},
            "startup": getattr(app.state.engine, "metrics", {})}, status_code=200 if state == "ready" else 503)

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": model, "object": "model", "owned_by": "local",
            "repository": repo, "revision": revision, "quantization": "8bit"}
            for model, repo, revision in [(ASR_ID, ASR_REPO, ASR_REV), (TTS_ID, TTS_REPO, TTS_REV)]]}

    @app.get("/v1/audio/voices")
    async def voices():
        return {"model": TTS_ID, "voices": VOICES, "default": "serena", "sample_rate": 24000}

    @app.get("/v1/requests/{request_id}")
    async def request_status(request_id: str):
        with lock:
            record = records.get(request_id)
            data = dict(record) if record else None
        return data if data else error(404, "unknown_request", "请求记录不存在或已过期。")

    def acquire(kind):
        with lock:
            if updating():
                raise AudioError(503, "updating", "正在准备更新，请稍后重试。")
            if app.state.status != "ready":
                raise AudioError(503, "not_ready", "模型尚未就绪。")
            if app.state.busy:
                raise AudioError(429, "busy", "STT／TTS 正在处理，请稍后重试。")
            app.state.busy = True
            cancelled = threading.Event()
            app.state.cancelled = cancelled
            request_id = uuid.uuid4().hex[:16]
            records[request_id] = {"request_id": request_id, "kind": kind, "status": "running"}
            while len(records) > 64:
                records.popitem(last=False)
        return request_id, cancelled

    def release(future):
        with lock:
            app.state.busy, app.state.cancelled = False, None
        # Retrieve failures even when the client disconnected, without logging private data.
        try:
            future.exception()
        except BaseException:
            pass

    def finish(request_id, **values):
        with lock:
            records[request_id].update(values)

    async def await_work(future, request, cancelled):
        wrapped = asyncio.wrap_future(future)
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        deadline = time.monotonic() + settings.timeout
        try:
            while not wrapped.done():
                if await request.is_disconnected():
                    cancelled.set()
                    raise AudioError(499, "cancelled", "请求已取消。")
                if time.monotonic() > deadline:
                    cancelled.set()
                    raise AudioError(504, "inference_timeout", "处理超时，请缩短输入。")
                await asyncio.wait([wrapped], timeout=0.1)
            return wrapped.result()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    def transcribe(payload, language, request_id, cancelled):
        begun = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="request-", dir=settings.temp_dir) as directory:
                source, normalized = Path(directory) / "upload.input", Path(directory) / "audio.wav"
                source.write_bytes(payload)
                duration, silent = decode(settings, source, normalized)
                if cancelled.is_set():
                    raise AudioError(499, "cancelled", "请求已取消。")
                started = time.monotonic()
                result = {"text": "", "language": language} if silent else app.state.engine.transcribe(normalized, language)
                result.update({"model": ASR_ID, "repository": ASR_REPO, "revision": ASR_REV,
                    "duration": round(duration, 4), "inference_ms": round((time.monotonic() - started) * 1000, 2),
                    "processing_ms": round((time.monotonic() - begun) * 1000, 2), "request_id": request_id, "no_speech": silent})
            finish(request_id, status="cancelled" if cancelled.is_set() else "completed", processing_ms=result["processing_ms"])
            LOG.info("request_done id=%s kind=stt ms=%.2f", request_id, result["processing_ms"])
            return result
        except Exception as exc:
            finish(request_id, status="failed", code=exc.code if isinstance(exc, AudioError) else "inference_failed")
            raise

    @app.exception_handler(AudioError)
    async def audio_error(_request, exc):
        return error(exc.status, exc.code, exc.message, headers={"Retry-After": "2"} if exc.status == 429 or exc.code == "updating" else {})

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request):
        # Validate first. Admission happens immediately before submitting to the only executor.
        try:
            async with request.form(max_files=1, max_fields=5) as form:
                audio = form.get("file")
                if audio is None or not hasattr(audio, "read"):
                    raise AudioError(400, "missing_audio", "请选择音频文件。")
                if form.get("model", ASR_ID) not in (ASR_ID, ASR_REPO):
                    raise AudioError(400, "invalid_model", "仅支持已部署的 ASR 模型。")
                language = form.get("language", "auto")
                if language not in ("auto", "zh", "en", "Chinese", "English", ""):
                    raise AudioError(400, "invalid_language", "语言请选择自动、中文或英文。")
                if form.get("response_format", "json") != "json":
                    raise AudioError(400, "invalid_format", "STT 仅支持 JSON 输出。")
                payload = await audio.read(MAX_UPLOAD + 1)
                if len(payload) > MAX_UPLOAD:
                    raise AudioError(413, "upload_too_large", "上传文件不能超过 20MiB。")
        except AudioError:
            raise
        except Exception:
            raise AudioError(400, "invalid_request", "音频上传请求无效。") from None
        language = {"zh": "Chinese", "en": "English", "auto": None, "": None}.get(language, language)
        request_id, cancelled = acquire("stt")
        future = worker.submit(transcribe, payload, language, request_id, cancelled)
        future.add_done_callback(release)
        try:
            return await await_work(future, request, cancelled)
        except AudioError:
            raise
        except Exception as exc:
            LOG.error("request_failed id=%s type=%s", request_id, type(exc).__name__)
            return error(500, "inference_failed", "识别失败，请重试。")

    def generate(speech, request_id, cancelled, output):
        begun, first, total = time.monotonic(), None, 0
        chunks = []
        try:
            for pcm in app.state.engine.synthesize(speech, cancelled, begun + settings.timeout):
                if cancelled.is_set():
                    raise AudioError(499, "cancelled", "请求已取消。")
                if time.monotonic() > begun + settings.timeout:
                    raise AudioError(504, "inference_timeout", "生成超时。")
                if first is None:
                    first = round((time.monotonic() - begun) * 1000, 2)
                total += len(pcm)
                if speech.stream:
                    # Bounded buffering. A slow/disconnected consumer cannot reserve memory forever.
                    while True:
                        if cancelled.is_set() or time.monotonic() > begun + settings.timeout:
                            raise AudioError(499, "cancelled", "请求已取消或客户端读取超时。")
                        try:
                            output.put(("chunk", pcm), timeout=0.1)
                            break
                        except queue.Full:
                            pass
                else:
                    chunks.append(pcm)
            if not total:
                raise AudioError(500, "empty_output", "模型未生成音频。")
            metrics = {"status": "completed", "duration": round(total / 48000, 4), "first_audio_ms": first,
                       "processing_ms": round((time.monotonic() - begun) * 1000, 2),
                       **getattr(app.state.engine, "memory", lambda: {})()}
            finish(request_id, **metrics)
            LOG.info("request_done id=%s kind=tts ms=%.2f audio_s=%.3f first_ms=%.2f", request_id, metrics["processing_ms"], metrics["duration"], first)
            if not speech.stream:
                pcm = b"".join(chunks)
                return (wav_bytes(pcm) if speech.response_format == "wav" else pcm), metrics
        except Exception as exc:
            finish(request_id, status="cancelled" if cancelled.is_set() else "failed", code=exc.code if isinstance(exc, AudioError) else "inference_failed")
            LOG.info("request_failed id=%s kind=tts type=%s", request_id, type(exc).__name__)
            if isinstance(exc, AudioError):
                raise
            raise AudioError(500, "inference_failed", "语音生成失败。") from None

    @app.post("/v1/audio/speech")
    async def speech(request: Request):
        try:
            value = Speech.model_validate(await request.json())
        except (ValueError, ValidationError):
            raise AudioError(400, "invalid_request", "请输入 1–500 字符文本，指令最多 200 字符；检查字段类型。") from None
        if not value.input.strip():
            raise AudioError(400, "empty_input", "文本不能为空。")
        value.voice = value.voice.lower()
        value.language = {"zh": "chinese", "en": "english", "Chinese": "chinese", "English": "english"}.get(value.language, value.language)
        if value.model not in (TTS_ID, TTS_REPO):
            raise AudioError(400, "invalid_model", "仅支持已部署的 TTS 模型。")
        if value.voice not in VOICES or value.language not in ("auto", "chinese", "english"):
            raise AudioError(400, "invalid_voice_or_language", "音色或语言无效。")
        if value.response_format not in ("wav", "pcm") or (value.stream and value.response_format != "pcm"):
            raise AudioError(400, "invalid_format", "支持 WAV、PCM；流式输出必须选择 PCM。")
        request_id, cancelled = acquire("tts")
        output = queue.Queue(maxsize=8)
        future = worker.submit(generate, value, request_id, cancelled, output)
        future.add_done_callback(release)
        headers = {"X-Request-ID": request_id, "X-Audio-Sample-Rate": "24000", "X-Audio-Channels": "1", "X-Audio-Format": "pcm_s16le", "X-Model": TTS_ID}
        if not value.stream:
            try:
                data, metrics = await await_work(future, request, cancelled)
            except AudioError:
                raise
            except Exception:
                return error(500, "inference_failed", "语音生成失败，请重试。")
            headers.update({"X-Processing-Ms": str(metrics["processing_ms"]), "X-First-Audio-Ms": str(metrics["first_audio_ms"]), "X-Audio-Duration": str(metrics["duration"])})
            return Response(data, media_type="audio/wav" if value.response_format == "wav" else "application/octet-stream", headers=headers)

        async def next_chunk(check_disconnect=False):
            while True:
                try:
                    return output.get_nowait()
                except queue.Empty:
                    if check_disconnect and await request.is_disconnected():
                        cancelled.set()
                        raise AudioError(499, "cancelled", "请求已取消。")
                    if future.done():
                        # The producer may finish while the disconnect check yields to the loop.
                        # Drain its final queued chunks before reporting completion or failure.
                        try:
                            return output.get_nowait()
                        except queue.Empty:
                            pass
                        future.result()  # Propagate failure: never mark partial audio successful.
                        return ("done", b"")
                    await asyncio.sleep(0.02)

        # Delay HTTP 200 until first audio is available, so early failures return structured JSON.
        try:
            async with asyncio.timeout(settings.timeout):
                first = await next_chunk(check_disconnect=True)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        except TimeoutError:
            cancelled.set()
            raise AudioError(504, "inference_timeout", "生成超时。") from None
        except AudioError:
            raise
        except Exception:
            return error(500, "inference_failed", "语音生成失败。")

        async def body():
            try:
                current = first
                while current[0] != "done":
                    yield current[1]
                    current = await next_chunk()
            finally:
                cancelled.set()
        return StreamingResponse(body(), media_type="application/octet-stream", headers=headers)

    @app.get("/")
    async def index():
        return HTMLResponse("<!doctype html><html lang='zh'><meta charset='utf-8'><title>CyberAudio</title><h1>CyberAudio</h1><p>请在 Mac 上打开 CyberAudio App。统一 API 已提供 STT 与 TTS。</p><p>就绪状态：/ready。API：/v1/audio/transcriptions、/v1/audio/speech。</p></html>")

    return app


def watch_parent(parent_pid):
    """stdin belongs exclusively to the app. EOF and PID checks cover normal/crash exits."""
    def terminate():
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(8)
        os._exit(0)

    def pipe_guard():
        try:
            while os.read(0, 64):
                pass
        finally:
            terminate()

    def pid_guard():
        while True:
            if os.getppid() != parent_pid:
                terminate()
                return
            time.sleep(1)
    threading.Thread(target=pipe_guard, daemon=True).start()
    threading.Thread(target=pid_guard, daemon=True).start()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--instance", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    root = args.root
    config = json.loads((root / "config/service.json").read_text())
    token = (root / "config/api-token").read_text().strip()
    if len(token) < 32 or os.getppid() != args.parent_pid:
        raise RuntimeError("Invalid app ownership or configuration")
    (root / "logs").mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(root / "logs/service.log", maxBytes=5 * 1024 * 1024, backupCount=4)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "TOKENIZERS_PARALLELISM": "false"})
    settings = Settings(asr_dir=Path(config["asr_dir"]), tts_dir=Path(config["tts_dir"]), token=token,
                        temp_dir=root / "tmp", ffmpeg=config["ffmpeg"], instance=args.instance)
    import uvicorn
    with (root / "config/backend.lock").open("w") as process_lock:
        fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        settings.temp_dir.mkdir(parents=True, exist_ok=True)
        cleanup_stale(settings.temp_dir)
        watch_parent(args.parent_pid)
        uvicorn.run(create_app(settings), host="127.0.0.1", port=config.get("port", 8765), workers=1,
                    access_log=False, log_config=None, timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()
