"""Independent array/NumPy checks, no model or MLX import and no inference."""
from datetime import datetime, timezone
from pathlib import Path
import ast
import hashlib
import json
import struct
import subprocess
import urllib.request

import numpy as np
from transformers.audio_utils import mel_filter_bank

base = Path(__file__).resolve().parent
project = base.parents[2]
read = lambda p: json.loads(Path(p).read_text())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
plan = read(base / 'plan.json')
runner = read(base / 'runner.json')
analysis = read(base / 'analysis-runner.json')
native = read(base / 'native/report.json')
report = read(base / 'comparison.json')
assert all(r['status'] == 'diagnostic_completed' and not r['model_loaded'] for r in [runner, analysis, native, report])
assert runner['source_sha256'] == sha(base / 'run-native.py')
assert analysis['source_sha256'] == sha(base / 'run-analysis.py')
assert analysis['analysis_source_sha256'] == report['source_sha256'] == sha(base / 'analyze.py')
assert all(r['plan_sha256'] == sha(base / 'plan.json') for r in [runner, analysis, native, report])
assert report['native_report_sha256'] == sha(base / 'native/report.json')
assert plan['protected_source_sha256'] == {p: sha(p) for p in plan['protected_source_sha256']}
assert len(runner['steps']) == 5 and all(s['status'] == 'completed' and s['exit_code'] == 0 for s in runner['steps'])
assert analysis['exit_code'] == 0
assert runner['caller_sha256'] == sha(base / 'MelDSPProbe.swift')
assert runner['executable_sha256'] == sha(base / 'native-build/mel-dsp-probe')
assert native['asr_generations'] == report['asr_generations'] == plan['asr_generations'] == 0


def arrays(path):
    data = Path(path).read_bytes()
    size = struct.unpack('<Q', data[:8])[0]
    header = json.loads(data[8:8 + size]); header.pop('__metadata__', None)
    result = {}
    for name, info in header.items():
        dtype = {'F32': '<f4', 'I32': '<i4', 'I64': '<i8'}[info['dtype']]
        start, end = info['data_offsets']
        value = np.frombuffer(data[8 + size + start:8 + size + end], dtype=dtype).reshape(info['shape'])
        assert np.isfinite(value).all()
        result[name] = value
    return result


def compare(a, b):
    assert a.shape == b.shape
    delta = a.astype(np.float64) - b.astype(np.float64)
    return {'shape': list(a.shape), 'values_exact': bool(np.array_equal(a, b)),
            'different_values': int(np.count_nonzero(delta)), 'max_abs': float(np.max(np.abs(delta))),
            'mean_abs': float(np.mean(np.abs(delta))), 'rms_abs': float(np.sqrt(np.mean(delta * delta)))}


def norm(value):
    log = np.log10(np.maximum(value, 1e-10)).astype(np.float32)
    return (np.maximum(log, log.max() - np.float32(8)) + np.float32(4)) / np.float32(4)


def fft(audio, window):
    padded = np.pad(audio, (200, 200), mode='reflect').astype(np.float64)
    count = 1 + (len(padded) - 400) // 160
    frames = np.lib.stride_tricks.sliding_window_view(padded, 400)[::160]
    assert len(frames) == count
    return np.fft.rfft(frames * window.astype(np.float64), axis=1).astype(np.complex64)[:-1]


assert sha(base / 'native/constants.safetensors') == native['constants_sha256']
assert (base / 'native/constants.safetensors').stat().st_size == native['constants_bytes']
constants = arrays(base / 'native/constants.safetensors')
reference = arrays(base / 'reference-constants.safetensors')
window64 = np.hanning(401)[:-1]
filters64 = mel_filter_bank(201, 128, 0.0, 8000.0, 16000, norm='slaney', mel_scale='slaney')
assert np.array_equal(window64.astype(np.float32), reference['window'])
assert np.array_equal(filters64.astype(np.float32), reference['filters'])
assert compare(constants['window'], reference['window']) == report['constants']['window_native_vs_reference_float32']
assert compare(constants['filters'], reference['filters']) == report['constants']['filters_native_vs_reference_float32']
snapshots = [{'file': 'native/constants.safetensors', 'sha256': native['constants_sha256'], 'bytes': native['constants_bytes']},
             {'file': 'reference-constants.safetensors', 'sha256': sha(base / 'reference-constants.safetensors'), 'bytes': (base / 'reference-constants.safetensors').stat().st_size}]
comparisons = 2
assert len(native['results']) == len(report['results']) == len(plan['cases']) == 12
for case, nr, row in zip(plan['cases'], native['results'], report['results'], strict=True):
    assert case['id'] == nr['id'] == row['id']
    assert nr['original_public_mel_exact'] and nr['original_decomposition_exact']
    assert row['original_reference_mel_exact'] and row['original_reference_decomposition_exact'] and row['original_native_mel_exact']
    path = base / 'native' / nr['file']
    assert sha(path) == nr['sha256'] and path.stat().st_size == nr['bytes']
    assert sha(case['native_file']) == case['native_sha256'] and sha(case['reference_file']) == case['reference_sha256']
    snapshots.append({'file': str(path.relative_to(base)), 'sha256': nr['sha256'], 'bytes': nr['bytes']})
    original = arrays(case['native_file'])
    original_ref = arrays(case['reference_file'])
    captured = arrays(path)
    a, b = original['mel'][0].T, original_ref['mel'][0].T
    assert np.array_equal(captured['mel'], a)
    audio = original['audio']
    ref_fft = fft(audio, window64)
    ref_power = np.abs(ref_fft, dtype=np.float64) ** 2
    assert np.array_equal(norm(ref_power @ filters64), b)
    same_window_fft = fft(audio, constants['window'])
    native_fft = captured['fft_real'].astype(np.complex64); native_fft.imag = captured['fft_imag']
    double_power = np.abs(native_fft, dtype=np.float64) ** 2
    stages = {
        'window_values': compare(constants['window'], reference['window']),
        'filter_values': compare(constants['filters'], reference['filters']),
        'fft_real_original_vs_reference': compare(native_fft.real, ref_fft.real),
        'fft_imag_original_vs_reference': compare(native_fft.imag, ref_fft.imag),
        'fft_real_same_native_window': compare(native_fft.real, same_window_fft.real),
        'fft_imag_same_native_window': compare(native_fft.imag, same_window_fft.imag),
        'power_original_vs_reference': compare(captured['power'], ref_power),
        'power_same_native_complex': compare(captured['power'], double_power),
        'mel_power_same_native_power_and_filters': compare(captured['mel_power'], captured['power'].astype(np.float64) @ constants['filters'].astype(np.float64)),
        'normalization_same_native_mel_power': compare(a, norm(captured['mel_power'].astype(np.float64)))}
    assert stages == row['stages']
    controls = {'original-decomposition': a, 'reference-window-only': captured['reference_window_mel'],
                'reference-filters-only': captured['reference_filters_mel'], 'reference-window-and-filters': captured['reference_constants_mel']}
    assert {k: compare(v, b) for k, v in controls.items()} == row['native_arms_vs_original_reference']
    ref_controls = {'original-decomposition': b,
        'native-window-only': norm((np.abs(same_window_fft, dtype=np.float64) ** 2) @ filters64),
        'native-filters-only': norm(ref_power @ constants['filters'].astype(np.float64)),
        'native-window-and-filters': norm((np.abs(same_window_fft, dtype=np.float64) ** 2) @ constants['filters'].astype(np.float64)),
        'native-complex-only': norm(double_power @ filters64),
        'native-power-only': norm(captured['power'].astype(np.float64) @ filters64),
        'native-power-and-filters': norm(captured['power'].astype(np.float64) @ constants['filters'].astype(np.float64)),
        'native-mel-power-only': norm(captured['mel_power'].astype(np.float64))}
    assert {k: compare(v, a) for k, v in ref_controls.items()} == row['reference_arms_vs_original_native']
    comparisons += len(stages) + len(controls) + len(ref_controls)
assert comparisons == 266
assert sum(p['bytes'] for p in snapshots) < plan['snapshot_budget_bytes']
processes = subprocess.check_output(['/bin/ps', '-axo', 'pid=,pgid=,command='], text=True).splitlines()
owned = [str(s['owned_group_pid']) for s in runner['steps']] + [str(analysis['owned_group_pid'])]
assert not any(pid in line.split()[:2] for pid in owned for line in processes)
formal = read(project / 'docs/evidence/robustness-2026-11-06/model-preservation/source-sha256.json')
assert formal == {p: sha(project / p) for p in formal}
protected = read(base.parent / 'power-kernel-20261107/generation-build/build.json')['preserved_input_sha256']
assert protected == {p: sha(p) for p in protected}
preserved = read(base.parent / 'model-preservation-20261107/fixed-result.json')
assert preserved['source_files_before'] == {p: sha(p) for p in preserved['source_files_before']}
assert preserved['frozen_before'] == {p: sha(p) for p in preserved['frozen_before']}
production = Path.home() / 'Library/Application Support/CyberAudio'
with urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=5) as response:
    state = json.load(response)
current = {'instance': state['instance'], 'status': state['status'], 'busy': state['busy'],
           'config_sha256': sha(production / 'config/service.json'), 'token_sha256': sha(production / 'config/api-token')}
assert current['status'] == 'ready' and not current['busy']
assert runner['production_before'] == runner['production_after'] == analysis['production_before'] == analysis['production_after'] == current
for p in base.glob('*.py'): ast.parse(p.read_text())
summary = {'status': 'verified', 'verified_at': datetime.now(timezone.utc).isoformat(),
           'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
           'model_loaded': False, 'asr_generations': 0, 'native_and_reference_mel_reproduction_cases': 12,
           'snapshots': snapshots, 'snapshot_bytes': sum(p['bytes'] for p in snapshots),
           'independent_recomputed_comparisons': comparisons,
           'window_float32_different_values': report['constants']['window_native_vs_reference_float32']['different_values'],
           'filters_float32_different_values': report['constants']['filters_native_vs_reference_float32']['different_values'],
           'native_arms_vs_original_reference': {arm: {'exact_cases': sum(r['native_arms_vs_original_reference'][arm]['values_exact'] for r in report['results']),
               'largest_case_max_abs': max(r['native_arms_vs_original_reference'][arm]['max_abs'] for r in report['results']),
               'unweighted_mean_of_case_mean_abs': sum(r['native_arms_vs_original_reference'][arm]['mean_abs'] for r in report['results']) / 12}
               for arm in plan['native_arms']},
           'formal_sources_verified': len(formal), 'original_model_files_verified': len(preserved['source_files_before']),
           'frozen_files_verified': len(preserved['frozen_before']), 'protected_inputs_verified': len(protected),
           'owned_processes_absent': True, 'production_preserved': True, 'formal_source_or_defaults_changed': False,
           'candidate_promoted': False, 'limits': plan['limits']}
path = base / 'verification.json'
assert not path.exists()
path.write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
print(json.dumps({k: v for k, v in summary.items() if k not in ['snapshots', 'limits', 'native_arms_vs_original_reference']}, indent=2))
