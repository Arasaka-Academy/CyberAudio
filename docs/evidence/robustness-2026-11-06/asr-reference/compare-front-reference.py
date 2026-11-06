"""Repeat original Python text, then capture public preprocessing/encoder boundaries."""
from datetime import datetime, timezone
from pathlib import Path
import dataclasses
import hashlib
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
prior = read(base / 'reference.json')
native = read(base / 'native-front/report.json')
assert native['status'] == prior['status'] == 'diagnostic_completed'
root = base / 'reference-front'
root.mkdir(exist_ok=False)
began = time.monotonic()
report = {'status': 'running', 'started': datetime.now(timezone.utc).isoformat(),
          'plan_sha256': sha(base / 'plan.json'), 'native_report_sha256': sha(base / 'native-front/report.json'),
          'reference_report_sha256': sha(base / 'reference.json'), 'source_sha256': sha(Path(__file__)),
          'no_reference_methods_replaced': True, 'results': []}


def save():
    report['elapsed_seconds'] = time.monotonic() - began
    (base / 'front-comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def compare(a, b):
    item = {'native_shape': list(b.shape), 'reference_shape': list(a.shape),
            'native_dtype': str(b.dtype), 'reference_dtype': str(a.dtype),
            'shape_equal': a.shape == b.shape, 'dtype_equal': a.dtype == b.dtype}
    if a.shape != b.shape:
        return item
    x, y = np.asarray(a.astype(mx.float32)).astype(np.float64), np.asarray(b.astype(mx.float32)).astype(np.float64)
    assert np.isfinite(x).all() and np.isfinite(y).all()
    delta = x - y
    item.update(values_exact=bool(np.array_equal(x, y)), different_values=int(np.count_nonzero(delta)),
                max_abs=float(np.max(np.abs(delta))), mean_abs=float(np.mean(np.abs(delta))),
                rms_abs=float(np.sqrt(np.mean(delta * delta))))
    return item


save()
try:
    mx.set_default_device(mx.gpu)
    mx.set_cache_limit(512 * 1024 * 1024)
    model = load_model((project / '.context/robustness/linked-models/asr').resolve(strict=True), lazy=False, strict=True)
    total_bytes = 0
    for case, old, nr in zip(plan['cases'], prior['results'], native['results'], strict=True):
        assert case == old['case'] and case['id'] == nr['id']
        started = time.monotonic()
        audio = Path(case['audio'])
        assert sha(audio) == case['audio_sha256']
        with wave.open(str(audio), 'rb') as sound:
            pcm = sound.readframes(sound.getnframes())
        values = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / np.float32(32768)
        assert hashlib.sha256(values.tobytes()).hexdigest() == old['input_float32_sha256'] == nr['input_float32_sha256']
        output = model.generate(values, max_tokens=1024, batch_size=1, temperature=0, top_p=1, top_k=0, min_p=0,
            repetition_penalty=1, repetition_context_size=32, language=None, prefill_step_size=2048,
            chunk_duration=1200, min_chunk_duration=1, verbose=False, stream=False, system_prompt=None)
        assert output.text == old['response']['text'], 'Original reference text changed before observation'
        mel, mask, count = model._preprocess_audio(values)
        prompt = model._build_prompt(count, None, None)
        encoded = model.get_audio_features(mel, mask)
        arrays = {'audio': mx.array(values), 'mel': mel, 'mask': mask, 'prompt': prompt, 'encoded': encoded}
        mx.eval(*arrays.values())
        path = root / (case['id'] + '.safetensors')
        mx.save_safetensors(str(path), arrays)
        total_bytes += path.stat().st_size
        assert total_bytes < 120 * 1024 * 1024
        native_path = base / 'native-front' / nr['file']
        assert sha(native_path) == nr['sha256']
        native_arrays = mx.load(str(native_path))
        comparisons = {k: compare(a, native_arrays[k]) for k, a in arrays.items()}
        report['results'].append({'id': case['id'], 'original_reference_text_exact': True,
            'num_audio_tokens': count, 'native_num_audio_tokens': nr['num_audio_tokens'],
            'file': path.name, 'sha256': sha(path), 'bytes': path.stat().st_size,
            'comparisons': comparisons, 'processing_seconds': time.monotonic() - started})
        save()
        print('FRONT', case['id'], 'mel', comparisons['mel'].get('max_abs'), 'encoded', comparisons['encoded'].get('max_abs'), flush=True)
        assert time.monotonic() - began < 600 and time.monotonic() - started < 90
        mx.clear_cache()
    report['status'] = 'diagnostic_completed'
except Exception as error:
    report.update(status='failed', error_type=type(error).__name__, error=str(error))
finally:
    report['finished'] = datetime.now(timezone.utc).isoformat()
    save()
raise SystemExit(0 if report['status'] == 'diagnostic_completed' else 1)
