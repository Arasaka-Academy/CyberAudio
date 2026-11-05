from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import asyncio
import json
import math
import struct
import threading
import time
import wave

from fastapi.testclient import TestClient
import httpx
import pytest

from server import ASR_ID, ASR_REV, TTS_ID, Settings, AudioError, create_app, cleanup_stale

TOKEN = "test-key-that-is-never-a-deployment-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def wav(seconds=0.12, silent=False):
    out = BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"".join(struct.pack("<h", 0 if silent else int(6000 * math.sin(i * 0.1))) for i in range(int(seconds * 16000))))
    return out.getvalue()


class Engine:
    def __init__(self):
        self.thread = threading.get_ident()
        self.started = threading.Event()
        self.release = threading.Event()
        self.block = self.fail = self.late_fail = False
        self.calls = 0

    def enter(self):
        assert threading.get_ident() == self.thread
        self.calls += 1
        self.started.set()
        if self.block:
            assert self.release.wait(5)
        if self.fail:
            raise RuntimeError("private filename, text and token must not leak")

    def transcribe(self, audio, language):
        self.enter()
        assert audio.is_file()
        return {"text": "打开 CyberAudio。", "language": "zh"}

    def synthesize(self, speech, cancelled, deadline):
        self.enter()
        for i in range(3):
            if cancelled.is_set():
                raise AudioError(499, "cancelled", "cancelled")
            if self.late_fail and i == 1:
                raise AudioError(422, "generation_limit", "shorten text")
            yield struct.pack("<h", i * 100 + 1) * 2400


@pytest.fixture
def running(tmp_path):
    engines = []
    def factory(_):
        engine = Engine()
        engines.append(engine)
        return engine
    settings = Settings(tmp_path / "asr", tmp_path / "tts", TOKEN, tmp_path / "tmp")
    app = create_app(settings, factory)
    with TestClient(app) as client:
        for _ in range(200):
            if client.get("/ready").status_code == 200:
                break
            time.sleep(0.01)
        assert client.get("/ready").status_code == 200
        yield client, engines[0], settings, app


def upload(client, payload=None, headers=AUTH, **fields):
    return client.post("/v1/audio/transcriptions", headers=headers,
        files={"file": ("speech.wav", wav() if payload is None else payload, "audio/wav")}, data=fields)


def speak(client, **fields):
    return client.post("/v1/audio/speech", headers=AUTH, json={"input": "你好。", **fields})


def test_auth_fixed_models_and_identity(running):
    client, engine, _, _ = running
    assert upload(client, headers={}).status_code == 401
    assert client.post("/v1/audio/speech", json={"input": "private"}).status_code == 401
    models = client.get("/v1/models", headers=AUTH).json()["data"]
    assert [m["id"] for m in models] == [ASR_ID, TTS_ID]
    assert models[0]["revision"] == ASR_REV
    assert speak(client, model="other/model").status_code == 400
    assert upload(client, model="other/model").status_code == 400
    assert engine.calls == 0
    assert len(client.get("/v1/audio/voices", headers=AUTH).json()["voices"]) == 9


def test_actual_ffmpeg_decode_compatibility_and_cleanup(running):
    client, engine, settings, _ = running
    result = upload(client, language="zh", model=ASR_ID, response_format="json")
    assert result.status_code == 200, result.text
    value = result.json()
    assert value["text"] == "打开 CyberAudio。"
    assert value["language"] == "zh"
    assert value["duration"] == pytest.approx(0.12, abs=0.01)
    assert value["revision"] == ASR_REV
    assert {"processing_ms", "inference_ms", "request_id", "no_speech"} <= value.keys()
    assert engine.calls == 1
    assert list(settings.temp_dir.iterdir()) == []


@pytest.mark.parametrize("fields", [{"language": "xx"}, {"response_format": "srt"}])
def test_stt_invalid_options(running, fields):
    assert upload(running[0], **fields).status_code == 400


def test_stt_invalid_oversize_long_silence(running):
    client, engine, settings, _ = running
    assert upload(client, payload=b"not audio").status_code == 400
    assert upload(client, payload=b"x" * (20 * 1024 * 1024 + 1)).status_code == 413
    assert upload(client, payload=wav(60.2, True)).status_code == 413
    result = upload(client, payload=wav(silent=True)).json()
    assert result["no_speech"] and result["text"] == ""
    assert engine.calls == 0
    assert list(settings.temp_dir.iterdir()) == []


@pytest.mark.parametrize("fields", [
    {"input": ""}, {"input": " "}, {"input": "x" * 501}, {"instructions": "x" * 201},
    {"input": 42}, {"voice": "unknown"}, {"language": "invalid"}, {"stream": "true"},
    {"response_format": "mp3"}, {"response_format": "wav", "stream": True}, {"speed": 2}
])
def test_tts_options_strictly_validated(running, fields):
    assert speak(running[0], **fields).status_code == 400
    assert running[1].calls == 0


def test_wav_and_pcm_stream_integrity(running):
    client, _, _, _ = running
    response = speak(client)
    assert response.status_code == 200, response.text
    with wave.open(BytesIO(response.content)) as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getnframes()) == (24000, 1, 2, 7200)
        pcm = audio.readframes(7200)
    streamed = speak(client, stream=True, response_format="pcm")
    assert streamed.status_code == 200
    assert streamed.content == pcm
    assert streamed.headers["x-audio-format"] == "pcm_s16le"
    record = client.get("/v1/requests/" + streamed.headers["x-request-id"], headers=AUTH).json()
    assert record["status"] == "completed" and record["duration"] == 0.3
    assert "input" not in record


def test_stt_tts_share_one_admission_slot(running):
    client, engine, _, _ = running
    engine.block = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(speak, client)
        assert engine.started.wait(3)
        assert upload(client).status_code == 429
        assert speak(client).status_code == 429
        engine.release.set()
        assert first.result(5).status_code == 200


UPDATE_AUTH = {**AUTH, "X-CyberAudio-Instance": "test", "X-CyberAudio-Update": "test-lease"}


def test_update_control_requires_auth_and_owned_instance(running):
    client, engine, _, _ = running
    for path in ["/internal/update/prepare", "/internal/update/cancel"]:
        assert client.post(path).status_code == 401
        assert client.post(path, headers=AUTH).status_code == 409
        assert client.post(path, headers={**AUTH, "X-CyberAudio-Instance": "other"}).status_code == 409
    assert speak(client).status_code == 200


def test_update_drains_existing_work_and_cancel_reopens_admission(running):
    client, engine, _, _ = running
    engine.block = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(speak, client)
        assert engine.started.wait(3)
        state = client.post("/internal/update/prepare", headers=UPDATE_AUTH)
        assert state.status_code == 200
        assert state.json() == {"instance": "test", "busy": True, "updating": True}
        for response in [speak(client), upload(client)]:
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "updating"
        engine.release.set()
        assert first.result(5).status_code == 200
    assert client.post("/internal/update/prepare", headers=UPDATE_AUTH).json()["busy"] is False
    assert client.get("/ready").json()["updating"] is True
    assert client.post("/internal/update/cancel", headers=UPDATE_AUTH).json()["updating"] is False
    assert client.post("/internal/update/cancel", headers=UPDATE_AUTH).status_code == 200
    assert speak(client).status_code == 200


def test_abandoned_update_lease_expires(running, monkeypatch):
    client, _, _, _ = running
    assert client.post("/internal/update/prepare", headers=UPDATE_AUTH).status_code == 200
    clock = time.monotonic()
    monkeypatch.setattr("server.time.monotonic", lambda: clock + 31)
    assert client.get("/ready").json()["updating"] is False
    assert speak(client).status_code == 200


def test_update_preparation_and_inference_are_atomically_ordered(running):
    client, engine, _, _ = running
    engine.block = True
    barrier = threading.Barrier(2)
    def start():
        barrier.wait()
        return speak(client)
    def prepare():
        barrier.wait()
        return client.post("/internal/update/prepare", headers=UPDATE_AUTH)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, preparing = pool.submit(start), pool.submit(prepare)
        state = preparing.result(3).json()
        assert state["updating"] is True
        assert speak(client).status_code == 503
        engine.release.set()
        assert first.result(5).status_code in (200, 503)
    assert client.post("/internal/update/prepare", headers=UPDATE_AUTH).json()["busy"] is False


def test_cancelled_or_old_control_cannot_pause_a_new_session(running):
    client, _, _, _ = running
    assert client.post("/internal/update/cancel", headers=UPDATE_AUTH).status_code == 200
    assert client.post("/internal/update/prepare", headers=UPDATE_AUTH).status_code == 409
    newer = {**UPDATE_AUTH, "X-CyberAudio-Update": "new-lease"}
    assert client.post("/internal/update/prepare", headers=newer).status_code == 200
    assert client.post("/internal/update/cancel", headers=UPDATE_AUTH).status_code == 200
    assert speak(client).status_code == 503
    assert client.post("/internal/update/cancel", headers=newer).status_code == 200
    assert speak(client).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_update_waits_until_audio_response_is_sent(running, stream):
    _, _, _, app = running
    sending, release_send = asyncio.Event(), asyncio.Event()
    messages = []
    sent_request = False
    payload = json.dumps({"input": "test", "stream": stream, "response_format": "pcm" if stream else "wav"}).encode()

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            sending.set()
            await release_send.wait()  # Real ASGI backpressure after inference has finished.

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": "/v1/audio/speech", "raw_path": b"/v1/audio/speech",
             "query_string": b"", "headers": [(b"authorization", f"Bearer {TOKEN}".encode()), (b"content-type", b"application/json")],
             "client": ("test", 1), "server": ("test", 80), "root_path": ""}
    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(sending.wait(), 3)
        for _ in range(100):
            if not app.state.busy: break
            await asyncio.sleep(0.01)
        assert not app.state.busy  # Model slot is free; HTTP audio is still being sent.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            prepared = await client.post("/internal/update/prepare", headers=UPDATE_AUTH)
            assert prepared.json()["busy"] is True
            assert (await client.post("/v1/audio/speech", headers=AUTH, json={"input": "new"})).status_code == 503
            release_send.set()
            await asyncio.wait_for(task, 3)
            assert (await client.post("/internal/update/prepare", headers=UPDATE_AUTH)).json()["busy"] is False
        body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        assert len(body) == (14400 if stream else 14444)
    finally:
        release_send.set()
        await asyncio.wait_for(task, 3)


@pytest.mark.asyncio
async def test_update_rejects_new_bodies_after_authentication(running):
    async def unread_body():
        raise AssertionError("Paused inference must not consume a new upload")
        yield b"unreachable"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=running[3]), base_url="http://test") as client:
        assert (await client.post("/internal/update/prepare", headers=UPDATE_AUTH)).status_code == 200
        for path in ["/v1/audio/speech", "/v1/audio/transcriptions"]:
            assert (await client.post(path, content=unread_body())).status_code == 401
            response = await client.post(path, headers=AUTH, content=unread_body())
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "updating"
            assert response.headers["retry-after"] == "2"


def test_private_errors_and_recovery(running):
    client, engine, settings, _ = running
    engine.fail = True
    for response in [upload(client), speak(client), speak(client, stream=True, response_format="pcm")]:
        assert response.status_code == 500
        assert "private" not in response.text
    assert list(settings.temp_dir.iterdir()) == []
    engine.fail = False
    assert speak(client).status_code == 200


def test_partial_tts_failure_is_not_a_success(running):
    client, engine, _, _ = running
    engine.late_fail = True
    assert speak(client).status_code == 422
    # ASGI aborts an already-started raw PCM response; clients must also read request status.
    with pytest.raises(Exception):
        speak(client, stream=True, response_format="pcm")


@pytest.mark.asyncio
async def test_disconnect_holds_slot_until_worker_finishes(running):
    _, engine, settings, app = running
    engine.block = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = asyncio.create_task(client.post("/v1/audio/transcriptions", headers=AUTH, files={"file": ("test.wav", wav())}))
        for _ in range(100):
            if engine.started.is_set(): break
            await asyncio.sleep(0.02)
        assert engine.started.is_set()
        first.cancel()
        with pytest.raises(asyncio.CancelledError): await first
        assert (await client.post("/v1/audio/speech", headers=AUTH, json={"input": "test"})).status_code == 429
        engine.release.set()
        for _ in range(100):
            if not app.state.busy: break
            await asyncio.sleep(0.02)
        assert not app.state.busy
        assert list(settings.temp_dir.iterdir()) == []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/internal/update/prepare", headers=UPDATE_AUTH)).json()["busy"] is False


def test_loading_failure_does_not_break_health(tmp_path):
    def fail(_): raise RuntimeError("private")
    settings = Settings(tmp_path, tmp_path, TOKEN, tmp_path / "tmp")
    with TestClient(create_app(settings, fail)) as client:
        for _ in range(100):
            if client.get("/ready").json()["status"] == "failed": break
            time.sleep(0.01)
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        assert speak(client).status_code == 503


def test_restart_cleanup_is_scoped(tmp_path):
    (tmp_path / "request-abandoned").mkdir()
    (tmp_path / "request-abandoned/upload").write_bytes(b"temporary audio")
    (tmp_path / "keep").mkdir()
    cleanup_stale(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep"]


@pytest.mark.asyncio
async def test_chunked_body_limit_before_json_parse(running):
    async def chunks():
        yield b"x" * 10000
        yield b"y" * 10000
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=running[3]), base_url="http://test") as client:
        response = await client.post("/v1/audio/speech", headers=AUTH, content=chunks())
        assert response.status_code == 413
    assert running[1].calls == 0
