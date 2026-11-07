"""Frozen paired context pilot; reference model methods and audio stay unchanged."""
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
PLAN = json.loads((ROOT / "protocol.json").read_text())
REPORT = ROOT / "inference.json"
RESULTS = ROOT / "results.private.jsonl"
BEGAN = time.monotonic()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def expired(*_):
    raise TimeoutError("Frozen context-pilot deadline exceeded; retain partial results")


for sig in [signal.SIGTERM, signal.SIGINT, signal.SIGALRM]:
    signal.signal(sig, expired)
assert not REPORT.exists() and not RESULTS.exists()
report = {
    "status": "preflight", "started_at": datetime.now(timezone.utc).isoformat(),
    "protocol_sha256": sha(ROOT / "protocol.json"), "script_sha256": sha(__file__),
    "versions": {p: importlib.metadata.version(p) for p in PLAN["reference_versions"]},
    "completed_generations": 0, "baseline_matches": 0, "retries": 0, "skipped": 0,
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
    for relative, digest in PLAN["reference_source_sha256"].items():
        assert sha(PROJECT / relative) == digest
    manifest_path = DATA / "ascend-private/test-manifest.json"
    assert sha(manifest_path) == PLAN["data"]["manifest_sha256"]
    manifest = json.loads(manifest_path.read_text())
    selected = [r for r in manifest if r["id"] in PLAN["data"]["ids"]]
    assert [r["id"] for r in selected] == PLAN["data"]["ids"]
    prior_path = PROJECT / PLAN["baseline"]["relative_path"]
    assert sha(prior_path) == PLAN["baseline"]["sha256"]
    prior = {r["id"]: r for r in map(json.loads, prior_path.read_text().splitlines())}
    mx.set_default_device(mx.gpu)
    mx.set_cache_limit(512 * 1024 * 1024)
    signal.alarm(PLAN["execution"]["individual_timeout_seconds"])
    model = load_model((PROJECT / ".context/robustness/linked-models/asr").resolve(strict=True), lazy=False, strict=True)
    signal.alarm(0)
    report["loaded_model_source_sha256"] = sha(inspect.getfile(type(model._model)))
    report["loaded_feature_extractor_sha256"] = sha(inspect.getfile(type(model._feature_extractor)))
    assert report["loaded_model_source_sha256"] == PLAN["reference_model_source_sha256"]
    assert report["loaded_feature_extractor_sha256"] == PLAN["reference_feature_extractor_sha256"]
    report.update(status="transcribing", model_load_seconds=round(time.monotonic() - BEGAN, 6))
    save()
    with RESULTS.open("x") as output:
        for arm in PLAN["execution"]["arms"]:
            if arm == "fixed-verbatim-context":
                assert report["baseline_matches"] == len(selected)
            context = None if arm == "baseline-empty-context" else PLAN["context_prompt"]
            for item in selected:
                assert time.monotonic() - BEGAN < PLAN["execution"]["total_timeout_seconds"]
                audio = DATA / "ascend-private" / item["audio"]
                assert sha(audio) == item["audio_sha256"]
                with wave.open(str(audio), "rb") as sound:
                    assert (sound.getnchannels(), sound.getsampwidth(), sound.getframerate(), sound.getnframes()) == (1, 2, 16000, item["frames"])
                    pcm = sound.readframes(sound.getnframes())
                assert hashlib.sha256(pcm).hexdigest() == item["pcm_sha256"]
                values = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / np.float32(32768)
                parameters = dict(PLAN["reference"], batch_size=1, language=None, verbose=False, stream=False, system_prompt=context)
                started = time.monotonic()
                signal.alarm(PLAN["execution"]["individual_timeout_seconds"])
                result = model.generate(values, **parameters)
                mx.synchronize()
                signal.alarm(0)
                response = dataclasses.asdict(result)
                elapsed = time.monotonic() - started
                row = {"arm": arm, "id": item["id"], "audio_sha256": item["audio_sha256"],
                       "pcm_sha256": item["pcm_sha256"], "input_float32_sha256": hashlib.sha256(values.tobytes()).hexdigest(),
                       "processing_seconds": elapsed, "response": response}
                output.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                output.flush()
                report["completed_generations"] += 1
                assert isinstance(result.text, str) and result.generation_tokens < parameters["max_tokens"]
                assert elapsed < PLAN["execution"]["individual_timeout_seconds"]
                if arm == "baseline-empty-context":
                    keys = PLAN["baseline"]["compare_fields"]
                    assert {k: response[k] for k in keys} == {k: prior[item["id"]]["response"][k] for k in keys}, item["id"]
                    report["baseline_matches"] += 1
                save()
                print(arm, report["completed_generations"], "/", PLAN["execution"]["total_generations"], flush=True)
                mx.clear_cache()
    report.update(status="pilot_complete_pending_scoring", results_sha256=sha(RESULTS))
except BaseException as error:
    report.update(status="failed", error_type=type(error).__name__, error=str(error))
finally:
    signal.alarm(0)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
print(json.dumps({k: report[k] for k in ["status", "completed_generations", "baseline_matches", "elapsed_seconds"]}), flush=True)
raise SystemExit(0 if report["status"] == "pilot_complete_pending_scoring" else 1)
