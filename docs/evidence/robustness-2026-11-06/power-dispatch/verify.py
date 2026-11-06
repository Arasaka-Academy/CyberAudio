"""Independent file, WAV, process, source/model and production checks; no inference."""
from datetime import datetime, timezone
from pathlib import Path
import ast
import hashlib
import json
import shlex
import socket
import struct
import subprocess
import urllib.request
import wave
import numpy as np

base = Path(__file__).resolve().parent
project = base.parents[2]
read = lambda p: json.loads(Path(p).read_text())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
scalar = read(base / 'scalar-comparison.json')
scalar_plan = read(base / 'plan.json')
scalar_runner = read(base / 'runner.json')
generation = read(base / 'generation-runner.json')
plan = read(base / 'generation-plan.json')
build = read(base / 'generation-build/build.json')
summary = read(base / 'summary.json')
asr_root = base / 'power-roundtrip'
asr = read(asr_root / 'reports/asr-followup.json')
assert scalar['status'] == 'verified' and scalar['model_loaded'] is False
assert scalar_runner['status'] == generation['status'] == summary['status'] == asr['status'] == 'diagnostic_completed'
assert scalar_runner['production_preserved'] and scalar_runner['shared_inputs_preserved']
assert generation['production_preserved'] and generation['shared_inputs_preserved']
assert asr['owned_helper_exit_code'] == 0 and asr['production_and_config_preserved'] and asr['temporary_request_cleanup']
assert scalar['plan_sha256'] == scalar_runner['plan_sha256'] == plan['scalar_plan_sha256'] == sha(base / 'plan.json')
assert generation['plan_sha256'] == build['plan_sha256'] == summary['generation_plan_sha256'] == asr['plan_sha256'] == sha(base / 'generation-plan.json')
assert scalar_runner['source_sha256'] == sha(base / 'build-control.py')
assert generation['source_sha256'] == sha(base / 'run-generations.py')
assert scalar['runner_sha256'] == sha(base / 'runner.json')
assert summary['generation_runner_sha256'] == asr['generation_runner_sha256'] == sha(base / 'generation-runner.json')
assert summary['asr_report_sha256'] == sha(asr_root / 'reports/asr-followup.json')
protected = build['preserved_input_sha256']
assert protected == {p: sha(p) for p in protected}
assert scalar_plan['preserved_input_sha256'] == {p: sha(p) for p in scalar_plan['preserved_input_sha256']}
assert build['status'] == 'built'
assert len(scalar_runner['steps']) == 5 and len(generation['steps']) == 4
for step in scalar_runner['steps'] + generation['steps']:
    assert step['status'] == 'completed' and step['exit_code'] == 0
    assert all(Path(a).is_file() for a in step['args'] if a.startswith('@') and a.endswith('.LinkFileList') for a in [a[1:]])
original_power = next(p for p in protected if p.endswith('/metal/jit_kernels.cpp.o'))
private_power = base / 'native-build/jit_kernels.cpp.o'
assert plan['private_power_object_sha256'] == sha(private_power)
for folder, left, right in [('native-build', 'control', 'precompiled-power'), ('generation-build', 'original-jit', 'precompiled-power')]:
    a = (base / folder / (left + '.LinkFileList')).read_text().splitlines()
    b = (base / folder / (right + '.LinkFileList')).read_text().splitlines()
    assert len(a) == len(b)
    assert [(x, y) for x, y in zip(a, b, strict=True) if x != y] == [(original_power, str(private_power))]
for label, arm in build['arms'].items():
    assert arm['executable_sha256'] == sha(base / 'generation-build' / label)
    assert arm['link_file_list_sha256'] == sha(base / 'generation-build' / (label + '.LinkFileList'))
initial = read(base / 'generation-build-first-failure/failure.json')
assert initial['status'] == 'failed_before_compile' and not initial['model_started'] and not initial['any_child_started']
assert initial['script_sha256'] == sha(base / 'generation-build-first-failure/run-generations.py')


def scalar_arrays(path):
    data = Path(path).read_bytes()
    size = struct.unpack('<Q', data[:8])[0]
    header = json.loads(data[8:8 + size])
    assert header.pop('__metadata__', None) is None
    result = {}
    for name, info in header.items():
        assert info['dtype'] == 'F32'
        a, b = info['data_offsets']
        arr = np.frombuffer(data[8 + size + a:8 + size + b], dtype='<f4').reshape(info['shape'])
        assert np.isfinite(arr).all()
        result[name] = arr
    return result


old = scalar_arrays(base / 'original-jit.safetensors')
new = scalar_arrays(base / 'precompiled-power.safetensors')
prior_root = base.parent / 'layer-parity-20261107'
native = scalar_arrays(prior_root / 'native-rope-formula.safetensors')
reference = scalar_arrays(prior_root / 'reference-rope-formula.safetensors')
for row in scalar['results']:
    name = row['tensor']
    a, b = old[name], new[name]
    assert np.array_equal(a, native[name]) and np.array_equal(b, reference[name])
    difference = a.astype(np.float64) - b.astype(np.float64)
    assert int(np.count_nonzero(difference)) == row['different_values']
    assert float(np.max(np.abs(difference))) == row['max_abs']
    assert hashlib.sha256(a.tobytes()).hexdigest() == row['original_float32_sha256']
    assert hashlib.sha256(b.tobytes()).hexdigest() == row['precompiled_float32_sha256']
provenance = read(base / 'dispatch-provenance.json')
assert provenance['private_source_sha256'] == scalar_plan['private_source_sha256'] == sha(base / 'native-build/jit_kernels.cpp')
assert provenance['native_compile_version_flags'] == ['-DMLX_VERSION="0.32.0"']
assert sha(project / 'native/Package.resolved') == provenance['native_package_resolved_sha256']
assert sha(base.parent / 'reference-parity-20261107/runtime/lib/python3.13/site-packages/mlx/lib/libmlx.dylib') == provenance['reference_binary_sha256']
disassembly = read(base / 'reference-disassembly-receipt.json')
for name, key in [('reference-binary-disassembly.txt', 'full'), ('reference-power-dispatch.txt', 'selected')]:
    assert sha(base / name) == disassembly[key + '_sha256']
    assert (base / name).stat().st_size == disassembly[key + '_bytes']

historical_root = base.parent / 'm4-acceptance-20261106/m1-text-layout-probe'
historical = read(historical_root / 'report.json')
assert sha(historical_root / 'report.json') == plan['baseline_report_sha256']
all_sources = {}
eos = read(project / '.context/robustness/linked-models/tts/config.json')['talker_config']['codec_eos_token_id']
for label, group in [('original-jit', 'baseline'), ('precompiled-power', 'candidate')]:
    path = base / ('generation-' + label) / 'report.json'
    report = read(path)
    assert sha(path) == generation[label + '_report_sha256'] == summary['generation_report_sha256'][label] == asr['generation_report_sha256'][label]
    assert report['status'] == 'diagnostic_completed' and report['plan_sha256'] == sha(base / 'generation-plan.json')
    assert len(report['results']) == len(plan['cases']) == 6
    assert report['failure_repeat_tokens_exact'] and report['failure_repeat_pcm_exact']
    for index, row in enumerate(report['results']):
        assert row['case'] == plan['cases'][index] and row['id'] == row['case']['id']
        assert row['token_count'] == len(row['tokens']) <= 1200 and row['eos_seen'] == (row['tokens'][-1] == eos)
        raw = path.parent / row['pcm_file']
        assert sha(raw) == row['pcm_sha256'] and raw.stat().st_size == row['pcm_bytes']
        assert row['audio_seconds'] == raw.stat().st_size / 48000
        all_sources[group + '-' + row['id']] = (row, raw)
        if label == 'original-jit' and index < 5:
            original = [r for r in historical['results'] if r['mode'] == 'prefill'][index]
            assert row['tokens'] == read(historical_root / (original['id'] + '-tokens.json'))
            assert row['pcm_sha256'] == original['pcm_sha256'] == sha(historical_root / (original['id'] + '.pcm'))
    assert report['results'][0]['tokens'] == report['results'][-1]['tokens']
    assert report['results'][0]['pcm_sha256'] == report['results'][-1]['pcm_sha256']
assert len(all_sources) == summary['total_generations'] == 12
for pair in summary['pairs']:
    a, ap = all_sources['baseline-' + pair['id']]
    b, bp = all_sources['candidate-' + pair['id']]
    assert pair['main_tokens_exact'] == (a['tokens'] == b['tokens'])
    assert pair['pcm_exact'] == (a['pcm_sha256'] == b['pcm_sha256'])
    for group, row, raw in [('baseline', a, ap), ('candidate', b, bp)]:
        assert pair[group] == {k: row[k] for k in ['token_count', 'eos_seen', 'audio_seconds']}
        observed = sorted([r for r in asr['results'] if r['source_id'] == group + '-' + pair['id'] and r['asr_language'] == 'auto'], key=lambda r: r['part'])
        assert pair[group + '_auto_asr'] == [{'part': r['part'], 'text': r['response']['text'], 'detected_language': r['response'].get('language')} for r in observed]
        value = np.fromfile(raw, dtype='<i2').astype(np.float64) / 32768
        assert float(np.sqrt(np.mean(value * value))) == pair[group + '_signal']['rms_full_scale']
    assert pair['auto_asr_results_exact'] and pair['baseline_auto_asr'] == pair['candidate_auto_asr']
    if 'known-failure' in pair['id']:
        assert pair['baseline']['token_count'] == 1200 and not pair['baseline']['eos_seen']
        assert pair['candidate']['token_count'] == 1060 and pair['candidate']['eos_seen']
        assert pair['candidate']['audio_seconds'] == 84.72
        assert all(not r['text'] for r in pair['candidate_auto_asr'])
assert not summary['candidate_promoted'] and not summary['formal_source_or_defaults_changed']
manifest = read(asr_root / 'corpus.json')
assert len(manifest) == len(asr['results']) == asr['expected_requests'] == 28
unique_wavs = set()
for item, row in zip(manifest, asr['results'], strict=True):
    assert item['id'] == row['id'] and item['language'] == row['asr_language'] and row['http_status'] == 200
    source, raw = all_sources[item['source_id']]
    assert item['source_pcm_sha256'] == source['pcm_sha256']
    path = Path(item['audio'])
    assert path.parent == asr_root / 'audio' and sha(path) == item['audio_sha256']
    with wave.open(str(path), 'rb') as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) == (1, 2, 24000, 'NONE')
        payload = wav.readframes(wav.getnframes())
    start = int(item['start_seconds'] * 48000)
    assert payload == raw.read_bytes()[start:start + len(payload)]
    assert len(payload) / 48000 == item['duration_seconds']
    unique_wavs.add(str(path))
assert len(unique_wavs) == 24
assert sha(project / '.context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app/Contents/Helpers/CyberAudioService') == asr['helper_sha256'] == '46083a4f7ba269b7f28377bd9532ede44ac0a812a55a55d1e1f0a9bd225cf86d'
assert not list((asr_root / 'tmp').glob('request-*'))
with socket.socket() as probe:
    assert probe.connect_ex(('127.0.0.1', asr['port'])) != 0
processes = subprocess.check_output(['/bin/ps', '-axo', 'pid=,pgid=,command='], text=True).splitlines()
for step in scalar_runner['steps'] + generation['steps']:
    assert not any(str(step['owned_group_pid']) in row.split()[:2] for row in processes)
assert not any(str(asr['owned_helper_pid']) == row.split()[0] for row in processes)
formal = read(project / 'docs/evidence/robustness-2026-11-06/model-preservation/source-sha256.json')
assert formal == {p: sha(project / p) for p in formal}
preserved = read(base.parent / 'model-preservation-20261107/fixed-result.json')
assert preserved['source_files_before'] == {p: sha(p) for p in preserved['source_files_before']}
assert preserved['frozen_before'] == {p: sha(p) for p in preserved['frozen_before']}
production = Path.home() / 'Library/Application Support/CyberAudio'
with urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=5) as response:
    state = json.load(response)
current = {'instance': state['instance'], 'status': state['status'], 'busy': state['busy'],
           'config_sha256': sha(production / 'config/service.json'), 'token_sha256': sha(production / 'config/api-token')}
for receipt in [scalar_runner, generation]:
    assert receipt['production_before'] == receipt['production_after'] == current
for key, value in asr['production_before'].items():
    assert asr['production_after'][key] == current[key] == value
for path in base.rglob('*.py'):
    ast.parse(path.read_text())
result = {'status': 'verified', 'verified_at': datetime.now(timezone.utc).isoformat(),
          'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
          'scalar_elements_each_tensor': 64, 'scalar_precompiled_all_reference_values_exact': True,
          'scalar_original_jit_all_original_values_exact': True, 'single_private_dispatch_patch_only': True,
          'historical_baseline_token_and_pcm_parity_cases': 5, 'fresh_generations': 12,
          'all_repeat_tokens_and_pcm_exact': True, 'normal_control_main_tokens_exact': 4,
          'all_six_pairs_auto_asr_results_exact': True, 'asr_requests': 28, 'unique_wavs': 24,
          'all_wav_headers_hashes_and_pcm_payloads_exact': True,
          'candidate_seed196_audio_seconds': 84.72, 'candidate_seed196_asr_empty_in_both_repeats': True,
          'source_hashes_verified': len(formal), 'original_model_files_verified': len(preserved['source_files_before']),
          'frozen_files_verified': len(preserved['frozen_before']), 'protected_compiled_and_source_inputs_verified': len(protected),
          'all_owned_processes_absent': True, 'asr_listener_closed': True, 'temporary_requests_empty': True,
          'production_preserved': True, 'preparation_failure_retained_no_child_started': True,
          'candidate_promoted': False, 'formal_source_or_defaults_changed': False,
          'limits': summary['limits']}
(base / 'verification.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
print(json.dumps({k: v for k, v in result.items() if k != 'limits'}, indent=2))
