"""Independent retained-file checks. No MLX import, model load, or inference."""
from datetime import datetime, timezone
from pathlib import Path
import ast
import hashlib
import json
import math
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
plan = read(base / 'plan.json')
native = read(base / 'native-http/reports/asr-followup.json')
reference = read(base / 'reference.json')
nf = read(base / 'native-front/report.json')
comparison = read(base / 'front-comparison.json')
injection = read(base / 'injection.json')
summary = read(base / 'summary.json')
runner_names = ['runner', 'front-runner', 'front-reference-runner', 'injection-runner']
runner_sources = ['run-all.py', 'run-front.py', 'run-front-reference.py', 'run-injection.py']
runners = [read(base / (name + '.json')) for name in runner_names]
assert len(plan['cases']) == 12 and len({c['id'] for c in plan['cases']}) == 12
assert all(r['status'] == 'diagnostic_completed' for r in [native, reference, nf, comparison, injection, summary] + runners)
for runner, source in zip(runners, runner_sources, strict=True):
    assert runner['source_sha256'] == sha(base / source)
    assert runner['plan_sha256'] == sha(base / 'plan.json')
    assert runner['production_preserved'] and runner['protected_inputs_preserved'] and runner['reference_sources_preserved']
    assert all(s['status'] == 'completed' and s['exit_code'] == 0 for s in runner['steps'])
assert [len(r['steps']) for r in runners] == [2, 3, 1, 1]
assert native['owned_helper_exit_code'] == 0 and native['temporary_request_cleanup'] and native['production_and_config_preserved']
assert native['expected_requests'] == native['expected_source_outputs'] == 12
assert all(r['plan_sha256'] == sha(base / 'plan.json') for r in [native, reference, nf, comparison, summary])
assert injection['plan_sha256'] == sha(base / 'injection-plan.json')
assert injection['original_plan_sha256'] == sha(base / 'plan.json')
ip = read(base / 'injection-plan.json')
assert ip['original_plan_sha256'] == sha(base / 'plan.json')
assert ip['cases'] == [c['id'] for c in plan['cases']]
assert reference['no_reference_methods_replaced'] and comparison['no_reference_methods_replaced']
assert reference['native_report_sha256'] == nf['native_report_sha256'] == sha(base / 'native-http/reports/asr-followup.json')
assert comparison['native_report_sha256'] == ip['native_front_report_sha256'] == sha(base / 'native-front/report.json')
assert comparison['reference_report_sha256'] == ip['reference_report_sha256'] == sha(base / 'reference.json')
assert ip['front_comparison_sha256'] == sha(base / 'front-comparison.json')
for report, source in [(reference, 'transcribe-reference.py'), (comparison, 'compare-front-reference.py'), (injection, 'inject-front-reference.py')]:
    assert report['source_sha256'] == sha(base / source)
assert summary['source_report_sha256'] == {p: sha(base / p) for p in summary['source_report_sha256']}
assert plan['source_sha256'] == {p: sha(project / p) for p in plan['source_sha256']}
assert runners[1]['caller_source_sha256'] == sha(base / 'ASRFrontProbe.swift')
assert runners[1]['executable_sha256'] == sha(base / 'front-build/asr-front-probe')


def arrays(path):
    data = Path(path).read_bytes()
    size = struct.unpack('<Q', data[:8])[0]
    assert 0 < size < len(data) - 8
    header = json.loads(data[8:8 + size])
    metadata = header.pop('__metadata__', None)
    assert metadata is None or isinstance(metadata, dict)
    types = {'F32': '<f4', 'BF16': '<u2', 'I32': '<i4', 'I64': '<i8'}
    values, dtypes, spans = {}, {}, []
    for name, info in header.items():
        start, end = info['data_offsets']
        assert 0 <= start <= end <= len(data) - 8 - size
        dtype = info['dtype']
        assert dtype in types
        arr = np.frombuffer(data, offset=8 + size + start, count=math.prod(info['shape']), dtype=types[dtype])
        assert arr.nbytes == end - start
        if dtype == 'BF16':
            arr = (arr.astype(np.uint32) << 16).view(np.float32)
        arr = arr.reshape(info['shape'])
        assert np.isfinite(arr).all()
        values[name], dtypes[name] = arr, dtype
        spans.append((start, end))
    ordered = sorted(spans)
    assert ordered[0][0] == 0 and ordered[-1][1] == len(data) - 8 - size
    assert all(a[1] == b[0] for a, b in zip(ordered, ordered[1:]))
    return values, dtypes


manifest = read(base / 'native-http/corpus.json')
expected_manifest = [dict(c, source_id=c['id'], part=0, language='auto') for c in plan['cases']]
assert manifest == expected_manifest
snapshots = []
all_metrics = []
pairs = []
arms = ip['arms']
assert arms == ['unmodified-repeat', 'native-mel-only', 'native-encoded-only']
for case, nr, rr, nfr, cr, ir, pair in zip(plan['cases'], native['results'], reference['results'], nf['results'], comparison['results'], injection['results'], summary['pairs'], strict=True):
    assert len({r['id'] for r in [case, nr, rr, nfr, cr, ir, pair]}) == 1
    assert rr['case'] == case and nr['http_status'] == 200 and nr['asr_language'] == 'auto'
    audio = Path(case['audio'])
    assert sha(audio) == case['audio_sha256']
    with wave.open(str(audio), 'rb') as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) == (1, 2, 16000, 'NONE')
        pcm = wav.readframes(wav.getnframes())
        assert wav.getnframes() / 16000 == case['duration_seconds']
    assert hashlib.sha256(pcm).hexdigest() == case['pcm_sha256']
    values = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / np.float32(32768)
    assert hashlib.sha256(values.tobytes()).hexdigest() == rr['input_float32_sha256'] == nfr['input_float32_sha256']
    # Same 16kHz/mono/PCM16 conversion and cap as production AudioFile.decode.
    decoded = subprocess.check_output(['/opt/homebrew/bin/ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
        '-protocol_whitelist', 'file,pipe', '-i', str(audio), '-map', '0:a:0', '-vn', '-t', '60.1',
        '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1'], timeout=15)
    assert decoded == pcm
    for group, row in [('native-front', nfr), ('reference-front', cr)]:
        path = base / group / row['file']
        assert path.name == case['id'] + '.safetensors'
        assert sha(path) == row['sha256'] and path.stat().st_size == row['bytes']
        snapshots.append({'path': str(path.relative_to(base)), 'sha256': row['sha256'], 'bytes': row['bytes']})
    na, nd = arrays(base / 'native-front' / nfr['file'])
    ra, rd = arrays(base / 'reference-front' / cr['file'])
    assert set(na) == set(ra) == {'audio', 'mel', 'mask', 'prompt', 'encoded'}
    assert np.array_equal(na['audio'], values) and np.array_equal(ra['audio'], values)
    assert nfr['native_http_text_exact'] and nfr['text'] == nr['response']['text']
    assert cr['original_reference_text_exact']
    assert nfr['num_audio_tokens'] == cr['num_audio_tokens'] == cr['native_num_audio_tokens']
    for name in na:
        a, b = na[name], ra[name]
        metric = cr['comparisons'][name]
        assert list(a.shape) == metric['native_shape'] and list(b.shape) == metric['reference_shape']
        assert (a.shape == b.shape) == metric['shape_equal']
        assert (nd[name] == rd[name]) == metric['dtype_equal']
        assert a.shape == b.shape
        x, y = a.astype(np.float32).astype(np.float64), b.astype(np.float32).astype(np.float64)
        delta = x - y
        recomputed = {'values_exact': bool(np.array_equal(x, y)), 'different_values': int(np.count_nonzero(delta)),
                      'max_abs': float(np.max(np.abs(delta))), 'mean_abs': float(np.mean(np.abs(delta))),
                      'rms_abs': float(np.sqrt(np.mean(delta * delta)))}
        assert recomputed == {key: metric[key] for key in recomputed}, (case['id'], name)
        all_metrics.append({'id': case['id'], 'tensor': name, **recomputed})
        if name in ['audio', 'mask', 'prompt']:
            assert recomputed['values_exact']
    assert ir['native_snapshot_sha256'] == nfr['sha256']
    assert [a['arm'] for a in ir['arms']] == arms
    old, new = nr['response']['text'], rr['response']['text']
    assert pair['native_text'] == old and pair['python_text'] == new
    assert pair['text_exact'] == (old == new)
    for arm in ir['arms']:
        text = arm['response']['text']
        assert 0 < arm['response']['generation_tokens'] < 1024
        assert arm['native_text_exact'] == (text == old)
        assert arm['original_reference_text_exact'] == (text == new)
        assert pair['injection_texts'][arm['arm']] == text
        assert pair['injection_matches_native'][arm['arm']] == (text == old)
    assert ir['arms'][0]['response']['text'] == new
    assert ir['arms'][2]['response']['text'] == old
    pairs.append(pair)
assert len(all_metrics) == 60 and len(snapshots) == 24
assert sum(m['values_exact'] for m in all_metrics) == 36
assert sum(p['text_exact'] for p in pairs) == summary['native_python_exact_texts'] == 10
assert sum(p['text_exact'] for p in pairs if p['group'] == 'mixed') == summary['mixed_exact_texts'] == 6
assert sum(p['text_exact'] for p in pairs if p['group'] == 'monolingual-control') == summary['monolingual_exact_texts'] == 4
assert sum(p['injection_matches_native']['native-mel-only'] for p in pairs) == summary['native_mel_injection_matches_native_text_cases'] == 11
assert all(p['injection_matches_native']['native-encoded-only'] for p in pairs)
noisy = pairs[1]
assert noisy['id'] == 'rms-composite-zh-002-1883-en-008-1846-snr20'
assert '桥下' in noisy['python_text'] and '桥下' not in noisy['native_text']
assert '桥下' not in noisy['injection_texts']['native-mel-only']
assert summary['actual_asr_generations'] == 12 + 12 + 12 + 12 + 36
assert not summary['candidate_promoted'] and not summary['formal_source_or_defaults_changed']
helper = project / '.context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app/Contents/Helpers/CyberAudioService'
assert sha(helper) == native['helper_sha256'] == plan['helper_sha256']
assert not list((base / 'native-http/tmp').glob('request-*'))
with socket.socket() as probe:
    assert probe.connect_ex(('127.0.0.1', native['port'])) != 0
processes = subprocess.check_output(['/bin/ps', '-axo', 'pid=,pgid=,command='], text=True).splitlines()
owned = [str(s['owned_group_pid']) for runner in runners for s in runner['steps']]
assert not any(pid in row.split()[:2] for pid in owned for row in processes)
assert not any(str(native['owned_helper_pid']) == row.split()[0] for row in processes)
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
for runner in runners:
    assert runner['production_before'] == runner['production_after'] == current
for key, value in native['production_before'].items():
    assert native['production_after'][key] == current[key] == value
for path in base.glob('*.py'):
    ast.parse(path.read_text())
result = {'status': 'verified', 'verified_at': datetime.now(timezone.utc).isoformat(),
          'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
          'model_loaded_by_verifier': False, 'inference_rerun': False,
          'input_wav_hashes_headers_pcm_and_production_decode_checks': 12,
          'snapshots': snapshots, 'snapshot_bytes': sum(p['bytes'] for p in snapshots),
          'boundary_comparisons_recomputed': 60, 'boundary_values_exact': 36,
          'native_repeat_texts_exact': 12, 'reference_repeat_texts_exact': 12,
          'causal_control_generations': 36, 'all_original_python_controls_reproduced': True,
          'native_mel_injection_matches_native': 11, 'native_encoded_injection_matches_native': 12,
          'noisy_case_omission_reproduced_by_native_mel': True,
          'same_mel_encoder_statistics_raw_array_retained': False,
          'formal_sources_verified': len(formal), 'original_model_files_verified': len(preserved['source_files_before']),
          'frozen_files_verified': len(preserved['frozen_before']), 'protected_compiled_and_source_inputs_verified': len(protected),
          'production_preserved': True, 'owned_processes_absent': True, 'asr_listener_closed': True,
          'request_directories_empty': True, 'formal_source_or_defaults_changed': False, 'candidate_promoted': False,
          'limits': summary['limits']}
output = base / 'verification.json'
assert not output.exists()
output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
print(json.dumps({k: v for k, v in result.items() if k not in ['snapshots', 'limits']}, indent=2))
