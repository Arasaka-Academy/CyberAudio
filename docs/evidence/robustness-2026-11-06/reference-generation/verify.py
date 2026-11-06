"""File-only verification of complete reference generations and decoder controls."""
from datetime import datetime, timezone
from pathlib import Path
import ast
import hashlib
import json
import socket
import subprocess
import urllib.request
import wave

import numpy as np

base = Path(__file__).resolve().parent
project = base.parents[2]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
plan = read(base / 'plan.json')
report = read(base / 'report.json')
runner = read(base / 'runner.json')
asr_root = base / 'reference-roundtrip'
asr = read(asr_root / 'reports/asr-followup.json')
adapter_plan = read(base / 'decoder-adapter-plan.json')
adapter_build = read(base / 'decoder-adapter-build/build.json')
native = read(base / 'decoder-native/report.json')
native_runner = read(base / 'decoder-native-runner.json')
first_build = read(base / 'decoder-build/build.json')
assert report['status'] == native['status'] == asr['status'] == 'diagnostic_completed'
assert len(report['results']) == 7 and len(report['decoder_results']) == 5 and len(native['results']) == 6
assert report['plan_sha256'] == runner['plan_sha256'] == sha(base / 'plan.json')
assert report['source_sha256'] == runner['source_sha256'] == sha(base / 'generate-reference.py')
assert first_build['status'] == 'failed' and first_build['shared_and_production_inputs_preserved']
assert first_build['source_sha256'] == sha(base / 'ReferenceDecodeProbe.swift')
assert 'Qwen3TTSSpeechTokenizer.decoder.getter' in (base / 'decoder-build/link.log').read_text()
assert not (base / 'decoder-build/reference-decode-probe').exists()
assert adapter_build['status'] == 'built' and adapter_build['shared_and_production_inputs_preserved']
assert native['plan_sha256'] == adapter_build['plan_sha256'] == sha(base / 'decoder-adapter-plan.json')
assert adapter_plan['first_attempt_plan_sha256'] == sha(base / 'decoder-followup-plan.json')
assert adapter_plan['first_attempt_build_sha256'] == sha(base / 'decoder-build/build.json')
protected = adapter_build['preserved_input_sha256']
assert protected == {p: sha(p) for p in protected}
sdk = next(Path(p) for p in protected if p.endswith('/Qwen3TTS.swift'))
assert (base / 'decoder-adapter-build/Qwen3TTS.swift').read_text() == sdk.read_text() + (base / 'decoder-adapter.swift').read_text()
assert adapter_build['source_sha256'] == native_runner['source_sha256'] == sha(base / 'ReferenceDecodeAdapterProbe.swift')
assert adapter_build['executable_sha256'] == sha(base / 'decoder-adapter-build/reference-decode-adapter-probe')
for path, digest in plan['source_sha256'].items():
    assert sha(project / path) == digest
assert report['actual_model_source_sha256'] == next(v for k, v in plan['source_sha256'].items() if k.endswith('/qwen3_tts.py'))
assert report['actual_decoder_source_sha256'] == next(v for k, v in plan['source_sha256'].items() if k.endswith('/speech_tokenizer.py'))
assert report['actual_sampling_source_sha256'] == next(v for k, v in plan['source_sha256'].items() if k.endswith('/sample_utils.py'))
decoder_source = sdk.with_name('Qwen3TTSSpeechTokenizer.swift')
assert sha(decoder_source) == adapter_plan['decoder_source_sha256']
assert native['native_sliding_window'] == 72
native_capture = project / plan['native_capture']
capture = read(native_capture / 'report.json')
prefix_samples = 0
for result in report['decoder_results']:
    original = next(x for x in capture['results'] if x['id'] == result['id'])
    before = native_capture / result['id'] / original['pcm_file']
    after = base / ('decoder-' + result['id']) / 'reference.pcm'
    float_path = after.with_name('reference.f32')
    assert sha(before) == original['pcm_sha256'] == result['native_pcm_sha256']
    assert sha(after) == result['reference_pcm_sha256'] and sha(float_path) == result['reference_f32_sha256']
    floats = np.frombuffer(float_path.read_bytes(), dtype='<f4')
    assert np.isfinite(floats).all()
    assert (np.clip(floats, -1, 1) * np.float32(32767)).astype('<i2').tobytes() == after.read_bytes()
    a = np.frombuffer(after.read_bytes(), dtype='<i2').astype(np.int32)
    b = np.frombuffer(before.read_bytes(), dtype='<i2').astype(np.int32)
    assert a.shape == b.shape and len(a) == result['samples']
    delta = a - b
    assert int(np.count_nonzero(delta)) == result['different_samples']
    assert int(np.max(np.abs(delta))) == result['maximum_pcm16_sample_delta'] == 1
    assert float(np.mean(np.abs(delta))) == result['mean_absolute_pcm16_sample_delta']
    assert result['codec_frames'] == original['token_count'] - int(original['eos_seen'])
    assert len(a) == result['codec_frames'] * 1920
    prefix_samples += len(a)
old_root = base.parent / 'sampling-parity-20261107'
order = read(old_root / 'order-generations/report.json')
paired = []
steps = 0
for case, row in zip(plan['cases'], report['results'], strict=True):
    assert case == row['case'] and case['id'] == row['id']
    path = base / row['id'] / 'reference.pcm'
    assert sha(path) == row['pcm_sha256'] and path.stat().st_size == row['pcm_bytes']
    assert row['audio_seconds'] == row['pcm_bytes'] / 48000
    assert row['pcm_bytes'] == row['audio_codec_frames'] * 1920 * 2
    assert sum(c['samples'] for c in row['chunks']) * 2 == row['pcm_bytes']
    if not case['observe_sampler']:
        assert row['eos_seen'] is None and row['sampled_steps'] is None
        continue
    codes_path = base / row['id'] / 'codes.json'
    codes = read(codes_path)
    assert sha(codes_path) == row['codes_sha256'] and len(codes) == row['sampled_steps']
    assert all(len(v) == 16 for v in codes)
    assert row['eos_seen'] and codes[-1][0] == 2150 and all(v[0] != 2150 for v in codes[:-1])
    assert len(codes) - 1 == row['audio_codec_frames']
    steps += len(codes)
    previous = next(x for x in order['results'] if x['id'] == row['id'])
    assert [v[0] for v in codes] == previous['tokens']
    decoded = next(x for x in native['results'] if x['id'] == row['id'])
    native_path = base / 'decoder-native' / (row['id'] + '.pcm')
    prior_path = old_root / 'order-generations' / previous['pcm_file']
    assert sha(native_path) == decoded['pcm_sha256'] and sha(prior_path) == previous['pcm_sha256']
    assert decoded['codes_sha256'] == row['codes_sha256'] and decoded['codec_frames'] == len(codes) - 1
    assert decoded['prior_order_pcm_exact'] == (native_path.read_bytes() == prior_path.read_bytes())
    a = np.frombuffer(native_path.read_bytes(), dtype='<i2').astype(np.int32)
    b = np.frombuffer(path.read_bytes(), dtype='<i2').astype(np.int32)
    assert a.shape == b.shape
    delta = np.abs(a - b)
    indices = np.flatnonzero(delta > 1)
    order_audio = np.frombuffer(prior_path.read_bytes(), dtype='<i2').astype(np.int32)
    assert order_audio.shape == a.shape
    order_delta = np.abs(a - order_audio)
    order_indices = np.flatnonzero(order_delta > 1)
    paired.append({'id': row['id'], 'reference_sampled_steps': len(codes), 'audio_seconds': row['audio_seconds'],
        'all_reference_and_order_main_tokens_equal': True, 'native_same_codes_equal_order_pcm': decoded['prior_order_pcm_exact'],
        'reference_vs_native_pcm_exact': native_path.read_bytes() == path.read_bytes(),
        'different_pcm16_samples': int(np.count_nonzero(delta)), 'maximum_pcm16_sample_delta': int(np.max(delta)),
        'first_sample_with_delta_over_one': int(indices[0]) if len(indices) else None,
        'first_codec_frame_with_delta_over_one': int(indices[0] // 1920) if len(indices) else None,
        'native_same_codes_vs_order_maximum_pcm16_delta': int(np.max(order_delta)),
        'native_same_codes_vs_order_first_frame_with_delta_over_one': int(order_indices[0] // 1920) if len(order_indices) else None,
        'chunk_maximum_pcm16_deltas': [int(np.max(delta[start:start+7680])) for start in range(0, len(delta), 7680)]})
first, repeat, direct = [next(r for r in report['results'] if r['id'] == key) for key in ['known-failure', 'known-failure-repeat', 'known-failure-unobserved']]
assert first['pcm_sha256'] == repeat['pcm_sha256'] == direct['pcm_sha256']
assert first['codes_sha256'] == repeat['codes_sha256']
assert report['failure_repeat_pcm_exact'] and report['failure_repeat_codes_exact'] and report['observer_unobserved_pcm_exact']
assert asr['owned_helper_exit_code'] == 0 and asr['temporary_request_cleanup'] and asr['production_and_config_preserved']
assert len(asr['results']) == asr['expected_requests'] == 9 and asr['expected_source_outputs'] == 7
manifest = read(asr_root / 'corpus.json')
assert len(manifest) == len(asr['results'])
for item, result in zip(manifest, asr['results'], strict=True):
    assert item['id'] == result['id'] and result['http_status'] == 200
    path = Path(item['audio'])
    assert sha(path) == item['audio_sha256']
    with wave.open(str(path), 'rb') as audio:
        assert audio.getnchannels() == 1 and audio.getsampwidth() == 2 and audio.getframerate() == 24000
        payload = audio.readframes(audio.getnframes())
    raw = base / item['source_id'].removeprefix('reference-') / 'reference.pcm'
    assert sha(raw) == item['source_pcm_sha256']
    start = int(item['start_seconds'] * 48000)
    assert payload == raw.read_bytes()[start:start+len(payload)]
assert not list((asr_root / 'tmp').glob('request-*'))
prior_asr = read(old_root / 'order-roundtrip/reports/asr-followup.json')
for pair in paired:
    old_text = [r['response']['text'] for r in prior_asr['results'] if r['source_id'] == 'candidate-' + pair['id'] and r['asr_language'] == 'auto']
    new_text = [r['response']['text'] for r in asr['results'] if r['source_id'] == 'reference-' + pair['id'] and r['asr_language'] == 'auto']
    assert len(old_text) == len(new_text) == 1
    pair['auto_asr_exactly_matches_order_candidate'] = old_text == new_text
with socket.socket() as connection:
    assert connection.connect_ex(('127.0.0.1', 18890)) != 0
for receipt in [runner, native_runner]:
    assert receipt['status'] == 'completed' and receipt['owned_process_exit_code'] == 0
    assert receipt['production_preserved'] and receipt['sdk_and_helpers_preserved']
processes = subprocess.check_output(['/bin/ps', '-axo', 'pid=,pgid=,command='], text=True).splitlines()
for pid in [runner['owned_group_pid'], native_runner['owned_group_pid'], asr['owned_helper_pid']]:
    assert not any(str(pid) in row.split()[:2] for row in processes)
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
for receipt in [runner, native_runner]:
    assert receipt['production_before'] == current == receipt['production_after']
assert all(asr['production_before'][k] == asr['production_after'][k] == current[k] for k in asr['production_before'])
for path in base.glob('*.py'):
    ast.parse(path.read_text())
result = {'status': 'verified', 'verified_at': datetime.now(timezone.utc).isoformat(),
    'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'reference_generations': 7, 'observed_reference_generations': 6, 'reference_sampled_steps': steps,
    'repeated_reference_codes_and_pcm_exact': True, 'observer_unobserved_pcm_exact': True,
    'decoder_prefix_cases': 5, 'decoder_prefix_pcm16_samples': prefix_samples, 'decoder_prefix_maximum_pcm16_delta': 1,
    'same_complete_codes_native_decode_cases': 6, 'paired_results': paired,
    'asr_requests': 9, 'wav_payload_references_verified': 9,
    'all_six_observed_auto_asr_match_order_candidate': all(p['auto_asr_exactly_matches_order_candidate'] for p in paired),
    'source_hashes_verified': len(formal), 'original_model_files_verified': len(preserved['source_files_before']),
    'frozen_files_verified': len(preserved['frozen_before']), 'source_bodies_preserved': True,
    'initial_native_link_failure_preserved': True, 'owned_processes_absent': True, 'owned_port_closed': 18890,
    'temporary_requests_removed': True, 'production_preserved': True,
    'default_changed': False, 'candidate_proposed': False,
    'limits': ['A few selected generation trajectories do not establish population failure rates or quality',
               'Main-token equality does not assert equality of uncaptured native auxiliary tokens',
               'Reference decoder uses an unbounded causal cache while native honors the configured 72-frame window; no decoder policy changed',
               'ASR roundtrip mixes synthesis and recognition errors; physical/listening and original strict M4 gates remain unaccepted']}
(base / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
print(json.dumps(result, ensure_ascii=False, indent=2))
