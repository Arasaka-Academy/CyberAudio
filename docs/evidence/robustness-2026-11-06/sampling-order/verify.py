"""Verify original sampler replay, matrix pairing, all audio payloads and preservation."""
from datetime import datetime, timezone
from pathlib import Path
import ast
import hashlib
import json
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
capture_root = base.parent / 'reference-parity-20261107/native-capture'
capture = read(capture_root / 'report.json')
native = read(base / 'native-filters/report.json')
comparison = read(base / 'comparison.json')
plan = read(base / 'matrix-plan.json')
summary = read(base / 'matrix-summary.json')
builds = [read(base / p / 'build.json') for p in ['native-build', 'order-build', 'matrix-build']]
assert all(b['status'] == 'built' and b['shared_and_production_inputs_preserved'] for b in builds)
protected = builds[0]['preserved_input_sha256']
assert protected == {p: sha(p) for p in protected}
sdk = next(Path(p) for p in protected if p.endswith('/Qwen3TTS.swift'))
source = sdk.read_text()
sampler = source[source.index('    func sampleToken('):source.index('    // MARK: - fromPretrained', source.index('    func sampleToken('))]
assert hashlib.sha256(sampler.encode()).hexdigest() == builds[0]['sampler_body_sha256']
assert sha(base / 'SamplingParityProbe.swift.in') == builds[0]['template_sha256']
assert sha(base / 'native-build/SamplingParityProbe.swift') == builds[0]['source_sha256']
expected_order = source.replace('        // Preserve EOS logit so top-k/top-p/min-p do not permanently suppress it.',
    '        logitsSlice = logitsSlice / temperature\n\n        // Preserve EOS logit so top-k/top-p/min-p do not permanently suppress it.', 1)
expected_order = expected_order.replace('        let token = categorical(filteredLogits / temperature)', '        let token = categorical(filteredLogits)', 1)
assert (base / 'order-build/Qwen3TTS.swift').read_text() == expected_order
vendor = base.parent / 'reference-parity-20261107/vendor/mlx_audio'
assert comparison['model_source_sha256'] == sha(vendor / 'tts/models/qwen3_tts/qwen3_tts.py')
assert comparison['actual_sampling_source_sha256'] == sha(vendor / 'lm/sample_utils.py')
assert comparison['categorical_module'] == 'mlx_audio.lm.sample_utils' and comparison['observer_control_exact']

def tensor_file(path, digest):
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest
    size = struct.unpack('<Q', data[:8])[0]
    header = json.loads(data[8:8+size])
    header.pop('__metadata__', None)
    arrays = {}
    for name, spec in header.items():
        start, end = spec['data_offsets']
        assert 0 <= start <= end <= len(data)-8-size
        raw = data[8+size+start:8+size+end]
        if spec['dtype'] == 'BF16':
            array = (np.frombuffer(raw, dtype='<u2').astype(np.uint32) << 16).view(np.float32)
        else:
            array = np.frombuffer(raw, dtype={'I32': '<i4', 'U32': '<u4', 'F32': '<f4'}[spec['dtype']])
        arrays[name] = array.reshape(spec['shape'])
    return arrays

calls = 0
metrics = []
filtered_files = 0
for raw_case, filtered_case, compared_case in zip(capture['results'], native['results'], comparison['results'], strict=True):
    assert raw_case['id'] == filtered_case['id'] == compared_case['id'] and filtered_case['all_codes_match']
    for raw_row, filtered_row, compared_row in zip(raw_case['steps'], filtered_case['steps'], compared_case['steps'], strict=True):
        raw = tensor_file(capture_root / raw_case['id'] / raw_row['file'], raw_row['sha256'])
        filtered = tensor_file(base / 'native-filters' / raw_case['id'] / filtered_row['file'], filtered_row['sha256'])
        filtered_files += 1
        assert raw['codes'].reshape(-1).tolist() == filtered_row['codes']
        assert len(compared_row['groups']) == 16
        for index, metric in enumerate(compared_row['groups']):
            assert metric['group'] == index and metric['native_code'] == filtered_row['codes'][index]
            assert metric['native_formula_exact']
            value = filtered['main'].reshape(-1) if index == 0 else filtered['auxiliary'][0,index-1,:]
            assert not np.isnan(value).any() and not np.isposinf(value).any()
            assert np.isfinite(value[metric['native_code']])
            assert int(np.isfinite(value).sum()) == metric['comparison']['native_finite']
            assert metric['comparison']['total_variation'] >= 0
            calls += 1
            metrics.append(metric)
assert calls == native['calls'] == 2336 and filtered_files == 146
assert sum(m['comparison']['values_exact'] for m in metrics) == comparison['all_summary']['exact_vectors'] == 2078
assert sum(m['native_code'] == m['reference_code_on_forced_path'] for m in metrics) == 2328
assert sum(not m['comparison']['values_exact'] for m in metrics if m['group'] != 0) == 136
original = read(project / '.context/robustness/m4-acceptance-20261106/m1-text-layout-probe/report.json')
preliminary = read(base / 'order-generations/report.json')
assert preliminary['status'] == 'diagnostic_completed' and len(preliminary['results']) == 6
assert preliminary['failure_repeat_tokens_exact'] and preliminary['failure_repeat_pcm_exact']
for row in preliminary['results']:
    path = base / 'order-generations' / row['pcm_file']
    assert path.stat().st_size == row['pcm_bytes'] and sha(path) == row['pcm_sha256'] and row['eos_seen']
known = preliminary['results'][0]
old_known = next(r for r in original['results'] if r['id'] == 'known-failure-prefill')
old_tokens = project / '.context/robustness/m4-acceptance-20261106/m1-text-layout-probe/known-failure-prefill-tokens.json'
assert sha(old_tokens) == old_known['tokens_sha256']
assert known['tokens'][:15] == read(old_tokens)[:15]
old_pcm = project / '.context/robustness/m4-acceptance-20261106/m1-text-layout-probe/known-failure-prefill.pcm'
assert (base / 'order-generations/known-failure.pcm').read_bytes()[:46080] == old_pcm.read_bytes()[:46080]
assert sha(base / 'order-generations/report.json') == plan['preliminary_candidate_report_sha256']
rows = []
for row in plan['reused_baselines']:
    assert not (row['language'] == 'english' and row['voice'] in {'eric','dylan'})
    assert sha(row['pcm_source']) == row['pcm_sha256'] and sha(row['tokens_source']) == row['tokens_sha256']
    rows.append((row['text_id'],row['voice'],row['seed'],'baseline'))
for stage, count in [('baseline',16),('candidate',144)]:
    root = base / ('matrix-'+stage)
    report = read(root / 'report.json')
    assert report['status'] == 'diagnostic_completed' and report['completed_generations'] == count
    assert report['plan_sha256'] == sha(base / 'matrix-plan.json')
    for row in report['results']:
        assert sha(root / (row['id']+'.pcm')) == row['pcm_sha256']
        assert sha(root / (row['id']+'-tokens.json')) == row['tokens_sha256']
        tokens = read(root / (row['id']+'-tokens.json'))
        assert len(tokens) == row['token_count']
        assert (root / (row['id']+'.pcm')).stat().st_size == row['pcm_bytes']
        assert row['audio_seconds'] == row['pcm_bytes']/48000
        rows.append((row['text_id'],row['voice'],row['seed'],stage))
    if stage == 'candidate':
        assert report['preliminary_candidate_parity']
        first = report['results'][0]
        assert first['pcm_sha256'] == known['pcm_sha256']
        assert read(root / (first['id']+'-tokens.json')) == known['tokens']
expected = {(r['text_id'],r['voice'],r['seed'],stage) for r in plan['cases'] for stage in ['baseline','candidate']}
assert len(rows) == len(set(rows)) == len(expected) == 288 and set(rows) == expected
wav_references = 0
asr_reports = []
for folder in ['order-roundtrip','matrix-roundtrip']:
    root = base / folder
    report = read(root / 'reports/asr-followup.json')
    asr_reports.append(report)
    assert report['status'] == 'diagnostic_completed' and report['owned_helper_exit_code'] == 0
    assert report['production_and_config_preserved'] and report['temporary_request_cleanup']
    assert len(report['results']) == report['expected_requests']
    corpus = read(root / 'corpus.json')
    assert len(corpus) == len(report['results'])
    for item, result in zip(corpus, report['results'], strict=True):
        assert result['id'] == item['id'] and result['http_status'] == 200
        path = Path(item['audio'])
        assert sha(path) == item['audio_sha256']
        with wave.open(str(path),'rb') as audio:
            assert audio.getnchannels() == 1 and audio.getsampwidth() == 2 and audio.getframerate() == 24000
            data = audio.readframes(audio.getnframes())
        source_id = item['source_id']
        if folder == 'order-roundtrip':
            if source_id.startswith('candidate-'):
                raw = base / 'order-generations' / (source_id.removeprefix('candidate-')+'.pcm')
            else:
                raw = project / '.context/robustness/m4-acceptance-20261106/m1-text-layout-probe' / (source_id.removeprefix('baseline-')+'-prefill.pcm')
        elif source_id.startswith('candidate-'):
            raw = base / 'matrix-candidate' / (source_id.removeprefix('candidate-')+'.pcm')
        else:
            name = source_id.removeprefix('baseline-')
            raw = base / 'matrix-baseline' / (name+'.pcm')
            if not raw.exists(): raw = project / '.context/robustness/m4-acceptance-20261106/m1-tts-matrix-r2' / (name+'-t7.pcm')
        assert sha(raw) == item['source_pcm_sha256']
        start = int(item['start_seconds']*48000)
        assert data == raw.read_bytes()[start:start+len(data)]
        wav_references += 1
    assert not list((root / 'tmp').glob('request-*'))
    with socket.socket() as connection:
        assert connection.connect_ex(('127.0.0.1',report['port'])) != 0
runners = [read(base / name) for name in ['native-runner.json','reference-runner.json','order-runner.json','matrix-runner.json']]
assert all(r['status'] == 'completed' and r['production_preserved'] and r['sdk_and_helpers_preserved'] for r in runners)
processes = subprocess.check_output(['/bin/ps','-axo','pid=,pgid=,command='],text=True).splitlines()
owned = [r['owned_group_pid'] for r in runners if 'owned_group_pid' in r]
owned += [r['owned_group_pid'] for r in runners[-1]['stages']]
owned += [r['owned_helper_pid'] for r in asr_reports]
for pid in owned:
    assert not any(str(pid) in row.split()[:2] for row in processes)
formal = read(project / 'docs/evidence/robustness-2026-11-06/model-preservation/source-sha256.json')
assert formal == {p:sha(project / p) for p in formal}
preserved = read(project / '.context/robustness/model-preservation-20261107/fixed-result.json')
assert preserved['source_files_before'] == {p:sha(p) for p in preserved['source_files_before']}
assert preserved['frozen_before'] == {p:sha(p) for p in preserved['frozen_before']}
production = Path.home() / 'Library/Application Support/CyberAudio'
with urllib.request.urlopen('http://127.0.0.1:8765/ready',timeout=5) as response: state=json.load(response)
current = {'instance':state['instance'],'status':state['status'],'busy':state['busy'],
           'config_sha256':sha(production/'config/service.json'),'token_sha256':sha(production/'config/api-token')}
assert all(r['production_before'] == current == r['production_after'] for r in runners)
for path in base.glob('*.py'): ast.parse(path.read_text())
report = {'status':'verified','verified_at':datetime.now(timezone.utc).isoformat(),
          'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
          'native_sample_replay_exact':calls,'native_filter_files_verified':filtered_files,
          'reference_sampler_actual_import_verified':True,'distribution_vectors_exact':2078,
          'auxiliary_support_differences':136,'source_order_change_only':True,
          'preliminary_generations':6,'reused_baselines':128,'fresh_current_route_baselines':16,
          'candidate_generations':144,'paired_cases':144,'wav_payload_references_verified':wav_references,
          'asr_requests':[len(r['results']) for r in asr_reports],
          'source_hashes_verified':len(formal),'original_model_files_verified':len(preserved['source_files_before']),
          'frozen_files_verified':len(preserved['frozen_before']),'owned_processes_absent':True,
          'owned_ports_closed':[18888,18889],'temporary_requests_removed':True,'production_preserved':True,
          'default_changed':False,'candidate_promoted':False,
          'limits':['Roundtrip combines TTS/ASR errors; no number normalization or listening acceptance',
                    'Original strict M4 workload, physical/interactive and public download/distribution gates remain unaccepted']}
(base/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
