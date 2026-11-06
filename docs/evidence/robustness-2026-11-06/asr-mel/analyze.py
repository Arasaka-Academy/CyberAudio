"""Model-free NumPy reference reproduction and single-boundary DSP controls."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import inspect
import json
import struct
import time

import numpy as np
from transformers import WhisperFeatureExtractor
from transformers.audio_utils import mel_filter_bank, spectrogram, window_function
from transformers.utils import is_torch_available

base = Path(__file__).resolve().parent
project = base.parents[2]
read = lambda p: json.loads(Path(p).read_text())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
plan = read(base / 'plan.json')
native = read(base / 'native/report.json')
assert native['status'] == 'diagnostic_completed' and not native['model_loaded']
assert plan['protected_source_sha256'] == {p: sha(p) for p in plan['protected_source_sha256']}
assert not is_torch_available(), 'Keep the original reference NumPy path'
began = time.monotonic()
report = {'status': 'running', 'started': datetime.now(timezone.utc).isoformat(),
          'model_loaded': False, 'asr_generations': 0, 'source_sha256': sha(Path(__file__)),
          'plan_sha256': sha(base / 'plan.json'), 'native_report_sha256': sha(base / 'native/report.json'),
          'reference_path': 'WhisperFeatureExtractor NumPy', 'results': []}
output = base / 'comparison.json'
assert not output.exists()


def save():
    report['elapsed_seconds'] = time.monotonic() - began
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')


def arrays(path):
    data = Path(path).read_bytes()
    size = struct.unpack('<Q', data[:8])[0]
    header = json.loads(data[8:8 + size]); header.pop('__metadata__', None)
    result = {}
    for name, info in header.items():
        dtype = {'F32': '<f4', 'I32': '<i4', 'I64': '<i8'}[info['dtype']]
        start, end = info['data_offsets']
        result[name] = np.frombuffer(data[8 + size + start:8 + size + end], dtype=dtype).reshape(info['shape'])
    return result


def compare(a, b):
    assert a.shape == b.shape and np.isfinite(a).all() and np.isfinite(b).all()
    delta = a.astype(np.float64) - b.astype(np.float64)
    return {'shape': list(a.shape), 'values_exact': bool(np.array_equal(a, b)),
            'different_values': int(np.count_nonzero(delta)), 'max_abs': float(np.max(np.abs(delta))),
            'mean_abs': float(np.mean(np.abs(delta))), 'rms_abs': float(np.sqrt(np.mean(delta * delta)))}


def reference_norm(mel_power):
    # Transformers logs in float64, casts log to float32, then clips/normalizes.
    value = np.log10(np.maximum(1e-10, mel_power)).astype(np.float32)
    value = np.maximum(value, value.max() - np.float32(8))
    return (value + np.float32(4)) / np.float32(4)


save()
try:
    constants = arrays(base / 'native/constants.safetensors')
    injected = arrays(base / 'reference-constants.safetensors')
    window64 = window_function(400, 'hann')
    filters64 = mel_filter_bank(201, 128, 0.0, 8000.0, 16000, norm='slaney', mel_scale='slaney')
    assert np.array_equal(injected['window'], window64.astype(np.float32))
    assert np.array_equal(injected['filters'], filters64.astype(np.float32))
    report['constants'] = {'window_native_vs_reference_float32': compare(constants['window'], injected['window']),
                           'filters_native_vs_reference_float32': compare(constants['filters'], injected['filters']),
                           'reference_window_compute_dtype': str(window64.dtype), 'reference_filters_compute_dtype': str(filters64.dtype)}
    extractor = WhisperFeatureExtractor(feature_size=128, sampling_rate=16000, hop_length=160, n_fft=400, dither=0.0)
    report['feature_extractor_source_sha256'] = sha(inspect.getfile(type(extractor)))
    for case, nr in zip(plan['cases'], native['results'], strict=True):
        assert case['id'] == nr['id'] and nr['original_public_mel_exact'] and nr['original_decomposition_exact']
        assert sha(case['native_file']) == case['native_sha256'] and sha(case['reference_file']) == case['reference_sha256']
        path = base / 'native' / nr['file']
        assert sha(path) == nr['sha256'] and path.stat().st_size == nr['bytes']
        prior_native = arrays(case['native_file'])
        prior_reference = arrays(case['reference_file'])
        captured = arrays(path)
        audio = prior_native['audio']
        original_reference = prior_reference['mel'][0].T
        original_native = prior_native['mel'][0].T
        observed = extractor(audio, sampling_rate=16000, return_attention_mask=True, truncation=False,
                             padding=True, return_tensors='np')['input_features'][0].T
        assert np.array_equal(observed, original_reference), 'Original reference Mel did not reproduce'
        assert np.array_equal(captured['mel'], original_native)
        complex_reference = spectrogram(audio, window64, frame_length=400, hop_length=160, power=None).T[:-1]
        reference_power = np.abs(complex_reference, dtype=np.float64) ** 2
        reference_mel = reference_norm(reference_power @ filters64)
        assert np.array_equal(reference_mel, original_reference), 'Reference decomposition did not reproduce'
        native_complex = captured['fft_real'].astype(np.complex64)
        native_complex.imag = captured['fft_imag']
        same_native_window_fft = spectrogram(audio, constants['window'], frame_length=400, hop_length=160, power=None).T[:-1]
        native_window_power = np.abs(same_native_window_fft, dtype=np.float64) ** 2
        native_fft_double_power = np.abs(native_complex, dtype=np.float64) ** 2
        stages = {
            'window_values': report['constants']['window_native_vs_reference_float32'],
            'filter_values': report['constants']['filters_native_vs_reference_float32'],
            'fft_real_original_vs_reference': compare(native_complex.real, complex_reference.real),
            'fft_imag_original_vs_reference': compare(native_complex.imag, complex_reference.imag),
            'fft_real_same_native_window': compare(native_complex.real, same_native_window_fft.real),
            'fft_imag_same_native_window': compare(native_complex.imag, same_native_window_fft.imag),
            'power_original_vs_reference': compare(captured['power'], reference_power),
            'power_same_native_complex': compare(captured['power'], native_fft_double_power),
            'mel_power_same_native_power_and_filters': compare(captured['mel_power'], captured['power'].astype(np.float64) @ constants['filters'].astype(np.float64)),
            'normalization_same_native_mel_power': compare(captured['mel'], reference_norm(captured['mel_power'].astype(np.float64)))}
        native_arms = {'original-decomposition': captured['mel'], 'reference-window-only': captured['reference_window_mel'],
                       'reference-filters-only': captured['reference_filters_mel'], 'reference-window-and-filters': captured['reference_constants_mel']}
        reference_arms = {'original-decomposition': reference_mel,
            'native-window-only': reference_norm(native_window_power @ filters64),
            'native-filters-only': reference_norm(reference_power @ constants['filters'].astype(np.float64)),
            'native-window-and-filters': reference_norm(native_window_power @ constants['filters'].astype(np.float64)),
            'native-complex-only': reference_norm(native_fft_double_power @ filters64),
            'native-power-only': reference_norm(captured['power'].astype(np.float64) @ filters64),
            'native-power-and-filters': reference_norm(captured['power'].astype(np.float64) @ constants['filters'].astype(np.float64)),
            'native-mel-power-only': reference_norm(captured['mel_power'].astype(np.float64))}
        row = {'id': case['id'], 'original_reference_mel_exact': True, 'original_reference_decomposition_exact': True,
               'original_native_mel_exact': True, 'stages': stages,
               'native_arms_vs_original_reference': {name: compare(value, original_reference) for name, value in native_arms.items()},
               'reference_arms_vs_original_native': {name: compare(value, original_native) for name, value in reference_arms.items()}}
        report['results'].append(row); save()
        assert time.monotonic() - began < plan['total_workload_budget_seconds']
        print('DSP', case['id'], 'max', [(name, item['max_abs']) for name,item in row['native_arms_vs_original_reference'].items()], flush=True)
    report['status'] = 'diagnostic_completed'
except Exception as error:
    report.update(status='failed', error_type=type(error).__name__, error=str(error))
finally:
    report['finished'] = datetime.now(timezone.utc).isoformat(); save()
raise SystemExit(0 if report['status'] == 'diagnostic_completed' else 1)
