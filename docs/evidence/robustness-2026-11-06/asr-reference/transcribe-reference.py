"""Unmodified Python ASR entry point over identical predeclared PCM16 samples."""
from datetime import datetime, timezone
from pathlib import Path
import dataclasses
import hashlib
import importlib.metadata
import inspect
import json
import time
import wave

import mlx.core as mx
import numpy as np
from mlx_audio.stt.utils import load_model

base = Path(__file__).resolve().parent
project = base.parents[2]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
plan = read(base / 'plan.json')
assert read(base / 'native-http/reports/asr-followup.json')['status'] == 'diagnostic_completed'
for path, digest in plan['source_sha256'].items():
    assert sha(project / path) == digest
output = base / 'reference.json'
assert not output.exists()
began = time.monotonic()
report = {'status': 'running', 'started': datetime.now(timezone.utc).isoformat(),
          'plan_sha256': sha(base / 'plan.json'), 'source_sha256': sha(Path(__file__)),
          'versions': {p: importlib.metadata.version(p) for p in ['mlx', 'mlx-audio', 'transformers', 'numpy']},
          'native_report_sha256': sha(base / 'native-http/reports/asr-followup.json'),
          'no_reference_methods_replaced': True, 'results': []}


def save():
    report['elapsed_seconds'] = time.monotonic() - began
    tmp = output.with_suffix('.tmp')
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    tmp.replace(output)


save()
try:
    mx.set_default_device(mx.gpu)
    mx.set_cache_limit(512 * 1024 * 1024)
    model = load_model((project / '.context/robustness/linked-models/asr').resolve(strict=True), lazy=False, strict=True)
    report['loaded_model_source_sha256'] = sha(inspect.getfile(type(model._model)))
    assert report['loaded_model_source_sha256'] == next(v for k, v in plan['source_sha256'].items() if k.endswith('/qwen3_asr.py'))
    report['feature_extractor_source_sha256'] = sha(inspect.getfile(type(model._feature_extractor)))
    report['tokenizer_class'] = type(model._tokenizer).__name__
    report['model_load_seconds'] = time.monotonic() - began
    save()
    for case in plan['cases']:
        assert time.monotonic() - began < plan['total_reference_budget_seconds']
        audio = Path(case['audio'])
        assert sha(audio) == case['audio_sha256']
        with wave.open(str(audio), 'rb') as sound:
            assert (sound.getnchannels(), sound.getsampwidth(), sound.getframerate()) == (1, 2, 16000)
            pcm = sound.readframes(sound.getnframes())
        assert hashlib.sha256(pcm).hexdigest() == case['pcm_sha256']
        values = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / np.float32(32768)
        assert len(values) / 16000 == case['duration_seconds'] and np.isfinite(values).all()
        started = time.monotonic()
        result = model.generate(values, max_tokens=plan['max_tokens'], batch_size=1,
            temperature=plan['temperature'], top_p=plan['top_p'], top_k=plan['top_k'], min_p=plan['min_p'],
            repetition_penalty=plan['repetition_penalty'], repetition_context_size=plan['repetition_context_size'],
            language=None, prefill_step_size=plan['prefill_step_size'], chunk_duration=plan['chunk_duration'],
            min_chunk_duration=plan['min_chunk_duration'], verbose=False, stream=False, system_prompt=None)
        mx.synchronize()
        elapsed = time.monotonic() - started
        row = {'id': case['id'], 'case': case, 'input_float32_sha256': hashlib.sha256(values.tobytes()).hexdigest(),
               'processing_seconds': elapsed, 'response': dataclasses.asdict(result)}
        report['results'].append(row)
        save()
        print('REFERENCE', case['id'], round(elapsed, 3), 'seconds', flush=True)
        assert elapsed < plan['individual_reference_budget_seconds']
        assert result.generation_tokens < plan['max_tokens']
        mx.clear_cache()
    assert len(report['results']) == 12
    report['status'] = 'diagnostic_completed'
except Exception as error:
    report.update(status='failed', error_type=type(error).__name__, error=str(error))
finally:
    report['finished'] = datetime.now(timezone.utc).isoformat()
    save()
print(report['status'], round(report['elapsed_seconds'], 2), 'seconds')
raise SystemExit(0 if report['status'] == 'diagnostic_completed' else 1)
