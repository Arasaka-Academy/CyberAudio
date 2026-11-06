"""Independent file-only boundary, injection, process and preservation checks."""
from datetime import datetime, timezone
from pathlib import Path
import ast
import hashlib
import json
import math
import struct
import subprocess
import urllib.request
import numpy as np

base = Path(__file__).resolve().parent
project = base.parents[2]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
plan = read(base / 'plan.json')
build = read(base / 'native-build/build.json')
first_build = read(base / 'native-build-first-failure/build.json')
native = read(base / 'native-capture/report.json')
reference = read(base / 'reference-comparison.json')
capture_root = base.parent / 'complete-codec-20261107'
capture = read(capture_root / 'native-capture/report.json')
prior_reference = read(capture_root / 'reference-comparison.json')
assert first_build['status'] == 'failed' and first_build['shared_and_production_inputs_preserved']
assert 'initializer for conditional binding must have Optional type' in (base / 'native-build-first-failure/module.log').read_text()
assert first_build['diagnostic_sdk_sha256'] == sha(base / 'native-build-first-failure/Qwen3TTS.swift')
assert first_build['source_sha256'] == sha(base / 'LayerParityProbe.swift')
assert build['status'] == 'built' and build['shared_and_production_inputs_preserved']
assert build['executable_sha256'] == sha(base / 'native-build/layer-parity-probe')
assert build['diagnostic_sdk_sha256'] == sha(base / 'native-build/Qwen3TTS.swift')
assert build['diagnostic_talker_sha256'] == sha(base / 'native-build/Qwen3TTSTalker.swift')
assert build['preserved_input_sha256'] == {p: sha(p) for p in build['preserved_input_sha256']}
assert native['status'] == reference['status'] == 'diagnostic_completed'
assert native['plan_sha256'] == reference['plan_sha256'] == build['plan_sha256'] == sha(base / 'plan.json')
assert native['capture_report_sha256'] == plan['capture_report_sha256'] == sha(capture_root / 'native-capture/report.json')
assert reference['native_report_sha256'] == sha(base / 'native-capture/report.json')
assert reference['reference_source_sha256'] == plan['python_model_source_sha256']
assert reference['reference_talker_source_sha256'] == sha(base.parent / 'reference-parity-20261107/vendor/mlx_audio/tts/models/qwen3_tts/talker.py')


def arrays(path):
    data = Path(path).read_bytes()
    header_size = struct.unpack('<Q', data[:8])[0]
    header = json.loads(data[8:8 + header_size])
    assert header.pop('__metadata__', None) is None
    result = {}
    dtype = {'F32': '<f4', 'I32': '<i4', 'U32': '<u4', 'BF16': '<u2'}
    for name, info in header.items():
        a, b = info['data_offsets']
        raw = data[8 + header_size + a:8 + header_size + b]
        arr = np.frombuffer(raw, dtype=dtype[info['dtype']]).reshape(info['shape'])
        if info['dtype'] == 'BF16':
            arr = (arr.astype(np.uint32) << 16).view(np.float32)
        assert np.isfinite(arr).all()
        result[name] = (arr, info['dtype'])
    return result


boundary_count = 0
exact_count = 0
snapshot_bytes = 0
steps = 0
summary = []
for case, nr, rr in zip(plan['cases'], native['results'], reference['results'], strict=True):
    case_id = case['id']
    assert case == nr['case'] == rr['case'] and case_id == nr['id'] == rr['id']
    assert nr['all_original_native_forward_exact'] and rr['all_observer_controls_exact']
    old = next(r for r in capture['results'] if r['id'] == case_id)
    old_ref = next(r for r in prior_reference['results'] if r['id'] == case_id)
    assert len(nr['steps']) == len(rr['unobserved']) == len(rr['observed']) == old['token_count']
    for index, (ns, unobserved, observed) in enumerate(zip(nr['steps'], rr['unobserved'], rr['observed'], strict=True)):
        assert ns['step'] == unobserved['step'] == observed['step'] == index
        assert ns['logits_exact'] and ns['hidden_exact'] and observed['unobserved_forward_exact']
        assert unobserved['comparisons'] == {k: old_ref['steps'][index]['comparisons'][k] for k in ['logits', 'hidden']}
    assert [s['step'] for s in nr['snapshots']] == plan['trace_steps'][case_id]
    for ns, rs in zip(nr['snapshots'], rr['snapshots'], strict=True):
        assert ns['step'] == rs['step'] and ns['file'] == rs['file']
        p = base / 'native-capture' / case_id / ns['file']
        q = base / 'reference-capture' / case_id / rs['file']
        assert sha(p) == ns['sha256'] == rs['native_sha256'] and p.stat().st_size == ns['bytes']
        assert sha(q) == rs['sha256'] and q.stat().st_size == rs['bytes']
        na, ra = arrays(p), arrays(q)
        assert set(na) == set(ra) == set(rs['comparisons']) and len(na) == ns['array_count'] == 396
        snapshot_bytes += p.stat().st_size + q.stat().st_size
        nonexact = []
        for name, (a, dt) in ra.items():
            b, ndt = na[name]
            cmp = rs['comparisons'][name]
            assert a.shape == b.shape and dt == ndt and cmp['shape_equal'] and cmp['dtype_equal']
            delta = a.astype(np.float64) - b.astype(np.float64)
            exact = bool(np.array_equal(a, b))
            assert exact == cmp['values_exact']
            assert float(np.max(np.abs(delta))) == cmp['max_abs']
            assert float(np.mean(np.abs(delta))) == cmp['mean_abs']
            assert float(np.sqrt(np.mean(delta * delta))) == cmp['rms_abs']
            exact_count += exact
            boundary_count += 1
            if not exact:
                nonexact.append(name)
        assert len(nonexact) == rs['nonexact_boundaries']
        if ns['step'] != 84:
            assert nonexact == ['inv_freq']
        else:
            assert len(nonexact) == 386 and 'cos' in nonexact and 'sin' not in nonexact
            assert na['position_ids'][0].reshape(-1).tolist() == [104, 104, 104]
            for name in ['layer-00.input', 'layer-00.attn.input', 'layer-00.attn.q_raw', 'layer-00.attn.k_raw', 'layer-00.attn.v_raw', 'layer-00.attn.q_norm', 'layer-00.attn.k_norm']:
                assert rs['comparisons'][name]['values_exact']
            assert not rs['comparisons']['layer-00.attn.q_rotary']['values_exact']
        assert rs['first_nonexact_boundary'] == 'inv_freq'
        summary.append({'id': case_id, 'step': ns['step'], 'absolute_position': na['position_ids'][0].reshape(-1).tolist(),
                        'nonexact_boundaries': len(nonexact), 'cos_exact': rs['comparisons']['cos']['values_exact'],
                        'sin_exact': rs['comparisons']['sin']['values_exact']})
    steps += len(nr['steps'])
assert boundary_count == reference['boundary_comparisons'] == 3168
assert exact_count == reference['exact_boundary_comparisons'] == 2390
assert steps == 263
injection_root = base / 'native-frequency-injection'
injection = read(injection_root / 'reference-comparison.json')
injection_plan = read(injection_root / 'plan.json')
injection_runner = read(injection_root / 'reference-runner.json')
assert injection['status'] == 'diagnostic_completed' and injection['only_in_memory_main_frequency_replaced']
assert injection['reference_source_sha256'] == plan['python_model_source_sha256']
assert injection['plan_sha256'] == injection_runner['plan_sha256'] == sha(injection_root / 'plan.json')
assert injection_plan['native_frequency_snapshot_sha256'] == sha(injection_plan['native_frequency_snapshot'])
assert injection['native_report_sha256'] == sha(capture_root / 'native-capture/report.json')
injection_comparisons = []
for row, old in zip(injection['results'], capture['results'], strict=True):
    assert row['case'] == old['case'] and len(row['steps']) == len(old['steps'])
    injection_comparisons.extend(row['prepared'].values())
    for index, item in enumerate(row['steps']):
        assert item['step'] == index and item['forced_codes'][0] == old['tokens'][index]
        assert item['forced_codes'] == read(capture_root / 'native-codes' / (row['id'] + '.json'))[index]
        injection_comparisons.extend(item['comparisons'].values())
assert len(injection_comparisons) == 1859
assert all(c['shape_equal'] and c['dtype_equal'] and c['values_exact'] and c['max_abs'] == c['mean_abs'] == c['rms_abs'] == 0 for c in injection_comparisons)
formula = read(base / 'rope-formula.json')
component = read(base / 'rope-components.json')
repair = read(base / 'native-rope-repair.json')
initial = read(base / 'native-rope-runner.json')
assert initial['compile_exit_code'] == 0 and initial['link_exit_code'] == 1
assert '_LayerParityProbe_main' in (base / 'rope-scalar-build/link.txt').read_text()
assert repair['status'] == 'diagnostic_completed' and repair['link_exit_code'] == repair['run_exit_code'] == 0
assert repair['shared_inputs_preserved'] and repair['retained_first_attempt_sha256'] == sha(base / 'native-rope-runner.json')
assert repair['output_sha256'] == component['native_snapshot_sha256'] == sha(base / 'native-rope-formula.safetensors')
assert component['reference_snapshot_sha256'] == sha(base / 'reference-rope-formula.safetensors')
ns = arrays(base / 'native-rope-formula.safetensors')
rs = arrays(base / 'reference-rope-formula.safetensors')
for item in component['results']:
    name = item['tensor']
    a, b = rs[name][0], ns[name][0]
    delta = a.astype(np.float64) - b.astype(np.float64)
    assert bool(np.array_equal(a, b)) == item['values_exact']
    assert int(np.count_nonzero(delta)) == item['different_values'] and float(np.max(np.abs(delta))) == item['max_abs']
assert [r['different_values'] for r in component['results']] == [0, 0, 46, 42]
native_frequency = arrays(base / 'native-capture/known-failure/step-084.safetensors')['inv_freq'][0]
reference_frequency = arrays(base / 'reference-capture/known-failure/step-084.safetensors')['inv_freq'][0]
assert np.array_equal(ns['inverse'][0], native_frequency) and np.array_equal(rs['inverse'][0], reference_frequency)
assert hashlib.sha256(native_frequency.tobytes()).hexdigest() == injection['replacement_frequency_float32_sha256']
assert hashlib.sha256(reference_frequency.tobytes()).hexdigest() == injection['original_frequency_float32_sha256']
assert len(formula['inv_freq_differences']) == 42 and formula['head_dim'] == 128 and formula['rope_theta'] == 1000000
assert formula['position_controls'][0]['cos']['exact'] and formula['position_controls'][1]['cos']['exact']
assert not formula['position_controls'][2]['cos']['exact'] and formula['position_controls'][2]['cos']['different_values'] == 1
receipts = [read(base / 'native-runner.json'), read(base / 'reference-runner.json'), injection_runner]
for receipt in receipts:
    assert receipt['status'] == 'completed' and receipt['exit_code'] == receipt['owned_process_exit_code'] == 0
    assert receipt['production_preserved'] and receipt['sdk_and_helpers_preserved']
assert receipts[1]['source_sha256'] == sha(base / 'compare-reference.py')
assert injection_runner['source_sha256'] == sha(injection_root / 'compare-reference.py')
processes = subprocess.check_output(['/bin/ps', '-axo', 'pid=,pgid=,command='], text=True).splitlines()
for receipt in receipts:
    assert not any(str(receipt['owned_group_pid']) in row.split()[:2] for row in processes)
for name in ['native-build/layer-parity-probe', 'rope-scalar-build/rope-scalar-probe']:
    assert not any(str(base / name) in row for row in processes)
formal = read(project / 'docs/evidence/robustness-2026-11-06/model-preservation/source-sha256.json')
assert formal == {p: sha(project / p) for p in formal}
preserved = read(project / '.context/robustness/model-preservation-20261107/fixed-result.json')
assert preserved['source_files_before'] == {p: sha(p) for p in preserved['source_files_before']}
assert preserved['frozen_before'] == {p: sha(p) for p in preserved['frozen_before']}
production = Path.home() / 'Library/Application Support/CyberAudio'
with urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=5) as response:
    state = json.load(response)
current = {'instance': state['instance'], 'status': state['status'], 'busy': state['busy'],
           'config_sha256': sha(production / 'config/service.json'), 'token_sha256': sha(production / 'config/api-token')}
assert all(r['production_before'] == current == r['production_after'] for r in receipts)
for path in base.rglob('*.py'):
    if 'native-build' not in str(path) and 'private-cache' not in str(path):
        ast.parse(path.read_text())
result = {'status': 'verified', 'verified_at': datetime.now(timezone.utc).isoformat(),
          'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
          'native_original_forward_replay_steps': steps, 'all_native_original_outputs_exact': True,
          'reference_unobserved_and_observed_replay_steps_each': steps, 'all_reference_observer_controls_exact': True,
          'native_snapshot_files': 8, 'reference_snapshot_files': 8, 'snapshot_bytes_verified': snapshot_bytes,
          'boundary_comparisons': boundary_count, 'exact_boundary_comparisons': exact_count,
          'nonexact_boundary_comparisons': boundary_count - exact_count, 'boundary_summary': summary,
          'inverse_frequency_different_values': 42, 'inverse_frequency_maximum_abs': 2.9802322387695312e-08,
          'first_propagating_position': 104, 'first_propagating_step_zero_based': 84,
          'causal_injection_cases': 6, 'causal_injection_steps': 367,
          'causal_injection_tensor_comparisons': 1859, 'causal_injection_all_exact': True,
          'scalar_indices_and_exponents_exact': True, 'scalar_power_different_values': 46,
          'scalar_inverse_different_values': 42, 'initial_wrapper_compile_failure_retained': True,
          'initial_scalar_link_failure_retained': True, 'all_owned_model_processes_absent': True,
          'source_hashes_verified': len(formal), 'original_model_files_verified': len(preserved['source_files_before']),
          'frozen_files_verified': len(preserved['frozen_before']), 'production_preserved': True,
          'model_or_default_or_installed_reference_source_changed': False,
          'limits': ['Causal injection explains these complete forward divergences, not all content or termination failures',
                     'Lower-level compiler/backend cause of primitive power difference remains unassigned',
                     'The injection is a diagnostic in Python memory, not an adopted Swift model patch',
                     'No new listening, strict-soak, public-download, device or distribution gate accepted']}
(base / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
print(json.dumps({k: v for k, v in result.items() if k not in ['boundary_summary', 'limits']}, ensure_ascii=False, indent=2))
