"""Unmodified pinned Python reference over the complete frozen ASCEND test split."""
from datetime import datetime, timezone
from pathlib import Path
import dataclasses
import hashlib
import importlib.metadata
import inspect
import json
import signal
import time
import wave

import mlx.core as mx
import numpy as np
from mlx_audio.stt.utils import load_model

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
DATA = ROOT.parent / "post-delivery-20261107"
NATIVE = ROOT.parent / "natural-reference-20261107/native-check"
PLAN = json.loads((ROOT / "protocol.json").read_text())
REPORT = ROOT / "reference.json"
BEGAN = time.monotonic()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def expired(*_):
    raise TimeoutError("Frozen reference execution deadline exceeded; retain partial data without retry")


signal.signal(signal.SIGTERM, expired)
signal.signal(signal.SIGINT, expired)
signal.signal(signal.SIGALRM, expired)
assert not REPORT.exists() and not (ROOT / "reference-results.private.jsonl").exists()
report = {
    "status": "preflight", "started_at": datetime.now(timezone.utc).isoformat(),
    "protocol_sha256": sha(ROOT / "protocol.json"), "script_sha256": sha(__file__),
    "versions": {p: importlib.metadata.version(p) for p in PLAN["reference_versions"]},
    "completed_utterances": 0, "retries": 0, "skipped": 0,
    "reference_methods_replaced": False,
}


def save():
    report["elapsed_seconds"] = round(time.monotonic() - BEGAN, 6)
    temporary = REPORT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(REPORT)


save()
try:
    assert report["versions"] == PLAN["reference_versions"]
    native = json.loads((NATIVE / "runner.json").read_text())
    assert native["status"] == "whole_test_transcribed_not_quality_accepted"
    assert native["owned_helper_exit_code"] == 0 and native["owned_listener_closed"]
    baseline = {r["id"]: r for r in map(json.loads, (DATA / "natural-baseline/results.jsonl").read_text().splitlines())}
    native_rows = list(map(json.loads, (NATIVE / "results.jsonl").read_text().splitlines()))
    assert [r["id"] for r in native_rows] == PLAN["native_check"]["ids"]
    keys = PLAN["native_check"]["compared_fields"]
    assert all({k: r["response"][k] for k in keys} == {k: baseline[r["id"]]["response"][k] for k in keys} and r["http_status"] == 200 for r in native_rows)
    report["native_baseline_check_sha256"] = sha(NATIVE / "results.jsonl")
    for relative, digest in PLAN["reference_source_sha256"].items():
        assert sha(PROJECT / relative) == digest, "Pinned reference source changed: " + relative
    manifest_path = DATA / "ascend-private/test-manifest.json"
    assert sha(manifest_path) == PLAN["data"]["manifest_sha256"]
    manifest = json.loads(manifest_path.read_text())
    assert len(manifest) == len({r["id"] for r in manifest}) == PLAN["data"]["count"]
    mx.set_default_device(mx.gpu)
    mx.set_cache_limit(512 * 1024 * 1024)
    signal.alarm(PLAN["execution"]["reference_individual_timeout_seconds"])
    model = load_model((PROJECT / ".context/robustness/linked-models/asr").resolve(strict=True), lazy=False, strict=True)
    signal.alarm(0)
    report["loaded_model_source_sha256"] = sha(inspect.getfile(type(model._model)))
    report["loaded_feature_extractor_sha256"] = sha(inspect.getfile(type(model._feature_extractor)))
    report["tokenizer_class"] = type(model._tokenizer).__name__
    assert report["loaded_model_source_sha256"] == PLAN["reference_model_source_sha256"]
    assert report["loaded_feature_extractor_sha256"] == PLAN["reference_feature_extractor_sha256"]
    assert report["tokenizer_class"] == "Qwen2Tokenizer"
    report.update(status="transcribing", model_load_seconds=round(time.monotonic() - BEGAN, 6))
    save()
    with (ROOT / "reference-results.private.jsonl").open("x") as output:
        for index, item in enumerate(manifest, 1):
            assert time.monotonic() - BEGAN < PLAN["execution"]["reference_total_timeout_seconds"]
            audio = DATA / "ascend-private" / item["audio"]
            assert sha(audio) == item["audio_sha256"]
            with wave.open(str(audio), "rb") as sound:
                assert (sound.getnchannels(), sound.getsampwidth(), sound.getframerate(), sound.getnframes()) == (1, 2, 16000, item["frames"])
                pcm = sound.readframes(sound.getnframes())
            assert hashlib.sha256(pcm).hexdigest() == item["pcm_sha256"]
            values = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / np.float32(32768)
            assert np.isfinite(values).all() and len(values) / 16000 == item["duration_seconds"]
            parameters = PLAN["reference"]
            started = time.monotonic()
            signal.alarm(PLAN["execution"]["reference_individual_timeout_seconds"])
            result = model.generate(
                values, max_tokens=parameters["max_tokens"], batch_size=1,
                temperature=parameters["temperature"], top_p=parameters["top_p"],
                top_k=parameters["top_k"], min_p=parameters["min_p"],
                repetition_penalty=parameters["repetition_penalty"],
                repetition_context_size=parameters["repetition_context_size"],
                language=None, prefill_step_size=parameters["prefill_step_size"],
                chunk_duration=parameters["chunk_duration"],
                min_chunk_duration=parameters["min_chunk_duration"],
                verbose=False, stream=False, system_prompt=None,
            )
            mx.synchronize()
            signal.alarm(0)
            elapsed = time.monotonic() - started
            row = {
                "id": item["id"], "audio_sha256": item["audio_sha256"],
                "pcm_sha256": item["pcm_sha256"],
                "input_float32_sha256": hashlib.sha256(values.tobytes()).hexdigest(),
                "processing_seconds": elapsed, "response": dataclasses.asdict(result),
            }
            output.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            output.flush()
            report["completed_utterances"] = index
            save()
            assert isinstance(result.text, str)
            assert elapsed < PLAN["execution"]["reference_individual_timeout_seconds"]
            assert result.generation_tokens < parameters["max_tokens"]
            if index % 50 == 0:
                print("REFERENCE", index, "/", len(manifest), round(time.monotonic() - BEGAN, 2), "seconds", flush=True)
            mx.clear_cache()
    report.update(status="complete_reference_not_quality_accepted", results_sha256=sha(ROOT / "reference-results.private.jsonl"))
except BaseException as error:
    report.update(status="failed", error_type=type(error).__name__, error=str(error))
finally:
    signal.alarm(0)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
print(json.dumps({k: report[k] for k in ["status", "completed_utterances", "elapsed_seconds"]}), flush=True)
raise SystemExit(0 if report["status"] == "complete_reference_not_quality_accepted" else 1)
