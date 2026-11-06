"""Separate single-tensor interventions; retain every original/reference control."""
from datetime import datetime, timezone
from pathlib import Path
from types import MethodType
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
read = lambda p: json.loads(Path(p).read_text())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
plan = read(base / 'plan.json')
control_plan = read(base / 'injection-plan.json')
prior = read(base / 'reference.json')
native = read(base / 'native-front/report.json')
assert read(base / 'front-reference-runner.json')['status'] == 'diagnostic_completed'
output = base / 'injection.json'
assert not output.exists()
began = time.monotonic()
report = {'status': 'running', 'started': datetime.now(timezone.utc).isoformat(),
          'plan_sha256': sha(base / 'injection-plan.json'), 'original_plan_sha256': sha(base / 'plan.json'),
          'source_sha256': sha(Path(__file__)), 'results': []}


def save():
    report['elapsed_seconds'] = time.monotonic() - began
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def compare(a, b):
    assert a.shape == b.shape and a.dtype == b.dtype
    x, y = np.asarray(a.astype(mx.float32)).astype(np.float64), np.asarray(b.astype(mx.float32)).astype(np.float64)
    delta = x - y
    return {'shape': list(a.shape), 'dtype': str(a.dtype), 'values_exact': bool(np.array_equal(x, y)),
            'max_abs': float(np.max(np.abs(delta))), 'different_values': int(np.count_nonzero(delta))}


save()
try:
    mx.set_default_device(mx.gpu)
    mx.set_cache_limit(512 * 1024 * 1024)
    model = load_model((project / '.context/robustness/linked-models/asr').resolve(strict=True), lazy=False, strict=True)
    internal = model._model
    original_preprocess = internal._preprocess_audio
    original_encoder = internal.get_audio_features
    for case, old, nr in zip(plan['cases'], prior['results'], native['results'], strict=True):
        started = time.monotonic()
        path = Path(case['audio'])
        assert sha(path) == case['audio_sha256']
        with wave.open(str(path), 'rb') as sound:
            values = np.frombuffer(sound.readframes(sound.getnframes()), dtype='<i2').astype(np.float32) / np.float32(32768)
        native_path = base / 'native-front' / nr['file']
        assert sha(native_path) == nr['sha256']
        arrays = mx.load(str(native_path))
        reference_mel, reference_mask, count = original_preprocess(values)
        assert count == nr['num_audio_tokens']
        reference_encoder_on_native_mel = original_encoder(arrays['mel'], reference_mask)
        mx.eval(reference_encoder_on_native_mel)
        row = {'id': case['id'], 'native_snapshot_sha256': nr['sha256'],
               'encoder_comparison_with_same_native_mel': compare(reference_encoder_on_native_mel, arrays['encoded']),
               'arms': []}
        report['results'].append(row)
        for arm in control_plan['arms']:
            assert internal._preprocess_audio.__func__ is original_preprocess.__func__
            assert internal.get_audio_features.__func__ is original_encoder.__func__
            try:
                if arm == 'native-mel-only':
                    def replace_mel(self, audio):
                        return arrays['mel'], reference_mask, count
                    internal._preprocess_audio = MethodType(replace_mel, internal)
                elif arm == 'native-encoded-only':
                    def replace_encoded(self, input_features, feature_attention_mask=None):
                        return arrays['encoded']
                    internal.get_audio_features = MethodType(replace_encoded, internal)
                arm_started = time.monotonic()
                result = model.generate(values, max_tokens=1024, batch_size=1, temperature=0, top_p=1, top_k=0, min_p=0,
                    repetition_penalty=1, repetition_context_size=32, language=None, prefill_step_size=2048,
                    chunk_duration=1200, min_chunk_duration=1, verbose=False, stream=False, system_prompt=None)
                mx.synchronize()
                row['arms'].append({'arm': arm, 'response': dataclasses.asdict(result),
                                    'processing_seconds': time.monotonic() - arm_started,
                                    'native_text_exact': result.text == nr['text'],
                                    'original_reference_text_exact': result.text == old['response']['text']})
                save()
                assert result.generation_tokens < 1024
                if arm == 'unmodified-repeat':
                    assert result.text == old['response']['text'], 'Original reference did not reproduce'
            finally:
                internal._preprocess_audio = original_preprocess
                internal.get_audio_features = original_encoder
            mx.clear_cache()
        assert time.monotonic() - began < 600 and time.monotonic() - started < 90
        print('INJECTION', case['id'], [(a['arm'], a['native_text_exact']) for a in row['arms']], flush=True)
    assert len(report['results']) == 12 and sum(len(r['arms']) for r in report['results']) == 36
    report['status'] = 'diagnostic_completed'
except Exception as error:
    report.update(status='failed', error_type=type(error).__name__, error=str(error))
finally:
    report['finished'] = datetime.now(timezone.utc).isoformat()
    save()
raise SystemExit(0 if report['status'] == 'diagnostic_completed' else 1)
