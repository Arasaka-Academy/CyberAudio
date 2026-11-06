"""File-only independent verification; no inference or audio regeneration."""
from datetime import datetime, timezone
from pathlib import Path
import ast
import hashlib
import json
import math
import struct
import subprocess
import urllib.request

base = Path(__file__).resolve().parent
project = base.parents[2]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
plan = read(base / 'plan.json')
native = read(base / 'native-capture/report.json')
reference = read(base / 'reference-comparison.json')
codec = read(base / 'code-comparison.json')
original_path = Path(plan['candidate_report'])
original = read(original_path)
ref_root = base.parent / 'reference-generation-20261107'
free = read(ref_root / 'report.json')
build = read(base / 'native-build/build.json')
assert all(x['status'] == 'diagnostic_completed' for x in [native, reference, codec])
assert sha(original_path) == plan['candidate_report_sha256'] == native['candidate_report_sha256']
assert sha(base / 'native-capture/report.json') == codec['native_report_sha256'] == reference['native_report_sha256']
assert codec['reference_report_sha256'] == sha(ref_root / 'report.json')
assert all(r['plan_sha256'] == sha(base / 'plan.json') for r in [native, reference, build])
assert build['status'] == 'built' and build['shared_and_production_inputs_preserved']
assert build['executable_sha256'] == sha(base / 'native-build/complete-codec-probe')
assert build['source_sha256'] == sha(base / 'CompleteCodecProbe.swift')
assert build['diagnostic_sdk_sha256'] == sha(base / 'native-build/Qwen3TTS.swift')
assert build['preserved_input_sha256'] == {p: sha(p) for p in build['preserved_input_sha256']}
assert reference['reference_source_sha256'] == plan['python_model_source_sha256']
snapshots = 0
snapshot_bytes = 0
steps = 0
comparisons = []
pairs = []
dtype = {'bfloat16': ('BF16', 2), 'int32': ('I32', 4), 'uint32': ('U32', 4)}


def load_snapshot(case_id, meta):
    global snapshots, snapshot_bytes
    path = base / 'native-capture' / case_id / meta['file']
    data = path.read_bytes()
    assert len(data) == meta['bytes'] and hashlib.sha256(data).hexdigest() == meta['sha256']
    header_bytes = struct.unpack('<Q', data[:8])[0]
    header = json.loads(data[8:8 + header_bytes])
    assert set(header) == set(meta['arrays'])
    end = 0
    for name, info in header.items():
        assert info['shape'] == meta['arrays'][name]['shape']
        code, width = dtype[meta['arrays'][name]['dtype']]
        assert info['dtype'] == code
        a, b = info['data_offsets']
        assert b - a == math.prod(info['shape']) * width
        end = max(end, b)
    assert len(data) == 8 + header_bytes + end
    snapshots += 1
    snapshot_bytes += len(data)
    payload = memoryview(data)[8 + header_bytes:]
    return {name: bytes(payload[a:b]) for name, info in header.items() for a, b in [info['data_offsets']]}


for case, row, rr, cr in zip(plan['cases'], native['results'], reference['results'], codec['results'], strict=True):
    case_id = case['id']
    assert case == row['case'] == rr['case'] and case_id == row['id'] == rr['id'] == cr['id']
    assert row['original_candidate_tokens_exact'] and row['original_candidate_pcm_exact'] and row['eos_seen']
    load_snapshot(case_id, row['prepared'])
    prior = next(r for r in original['results'] if r['id'] == case_id)
    pcm = base / 'native-capture' / case_id / row['pcm_file']
    prior_pcm = original_path.parent / prior['pcm_file']
    assert sha(pcm) == row['pcm_sha256'] == sha(prior_pcm) == prior['pcm_sha256']
    assert pcm.stat().st_size == row['pcm_bytes'] == (row['token_count'] - 1) * 1920 * 2
    assert row['tokens'] == prior['tokens'] and row['tokens'][-1] == 2150
    vectors = []
    for index, meta in enumerate(row['steps']):
        assert meta['step'] == index
        arrays = load_snapshot(case_id, meta)
        vectors.append(list(struct.unpack('<16I', arrays['codes'])))
    assert [v[0] for v in vectors] == row['tokens'] and len(vectors) == row['token_count']
    codes_path = base / 'native-codes' / (case_id + '.json')
    assert vectors == read(codes_path) and sha(codes_path) == cr['native_codes_sha256']
    free_row = next(r for r in free['results'] if r['id'] == case_id)
    reference_codes = ref_root / case_id / 'codes.json'
    assert sha(reference_codes) == free_row['codes_sha256'] == cr['reference_codes_sha256']
    expected = read(reference_codes)
    assert len(expected) == len(vectors) and [v[0] for v in expected] == row['tokens']
    differences = [{'step': i, 'group': j, 'native': a, 'reference': b, 'audio_frame': i < len(vectors) - 1}
                   for i, (v, w) in enumerate(zip(vectors, expected, strict=True))
                   for j, (a, b) in enumerate(zip(v, w, strict=True)) if a != b]
    assert differences == cr['differences'] and cr['all_groups_equal'] == (not differences) and cr['main_tokens_equal']
    pairs.append({'id': case_id, 'sampled_steps': len(vectors), 'all_main_tokens_equal': True,
                  'original_candidate_complete_pcm_exact': True, 'all_codec_values_equal': not differences,
                  'codec_differences_including_eos': len(differences),
                  'audio_codec_differences': sum(d['audio_frame'] for d in differences),
                  'eos_auxiliary_differences': sum(not d['audio_frame'] for d in differences),
                  'first_codec_difference': differences[0] if differences else None})
    assert len(rr['steps']) == len(vectors) == rr['completed_steps']
    comparisons.extend({'case': case_id, 'step': -1, 'tensor': name, **v} for name, v in rr['prepared'].items())
    for index, s in enumerate(rr['steps']):
        assert s['step'] == index and s['forced_codes'] == vectors[index]
        comparisons.extend({'case': case_id, 'step': index, 'tensor': name, **v} for name, v in s['comparisons'].items())
        assert all(s['comparisons'][k]['values_exact'] for k in ['input_embeds', 'next_input_embeds'])
    steps += len(vectors)
assert steps == codec['steps'] == 367 and steps * 16 == codec['codec_values'] == 5872
assert snapshots == 373 and snapshot_bytes == native['snapshot_bytes'] == 30490779
assert len(comparisons) == 1859
assert all(c['shape_equal'] and c['dtype_equal'] for c in comparisons)
different = [c for c in comparisons if not c['values_exact']]
assert len(different) == 42 and sum(c['values_exact'] for c in comparisons) == 1817
assert max(c['max_abs'] for c in comparisons) == 0.501953125
assert all(c['case'] in ['known-failure', 'known-failure-repeat'] and 84 <= c['step'] <= 90 and c['tensor'] in ['logits', 'hidden', 'code_logits'] for c in different)
for case_id in ['known-failure', 'known-failure-repeat']:
    d = [c for c in different if c['case'] == case_id]
    assert len(d) == 21 and min(c['step'] for c in d) == 84
assert [p['codec_differences_including_eos'] for p in pairs] == [97, 0, 0, 0, 0, 97]
receipts = [read(base / name) for name in ['native-runner.json', 'reference-runner.json']]
assert receipts[1]['source_sha256'] == sha(base / 'compare-reference.py')
assert receipts[1]['requirements_sha256'] == sha(base.parent / 'reference-parity-20261107/requirements.lock')
for r in receipts:
    assert r['status'] == 'completed' and r['owned_process_exit_code'] == r['exit_code'] == 0
    assert r['production_preserved'] and r['sdk_and_helpers_preserved']
processes = subprocess.check_output(['/bin/ps', '-axo', 'pid=,pgid=,command='], text=True).splitlines()
for receipt in receipts:
    assert not any(str(receipt['owned_group_pid']) in row.split()[:2] for row in processes)
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
for p in base.glob('*.py'):
    ast.parse(p.read_text())
result = {'status': 'verified', 'verified_at': datetime.now(timezone.utc).isoformat(),
          'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
          'cases': len(pairs), 'sampled_steps': steps, 'codec_values_compared': steps * 16,
          'snapshot_files_verified': snapshots, 'snapshot_bytes_verified': snapshot_bytes,
          'original_candidate_main_tokens_and_complete_pcm_exact': True,
          'pairs': pairs, 'tensor_comparisons': len(comparisons), 'exact_tensor_comparisons': 1817,
          'nonexact_tensor_comparisons': 42, 'maximum_absolute_tensor_difference': 0.501953125,
          'first_numerical_differences': [c for c in different if c['step'] == 84],
          'all_input_and_next_embeddings_exact': True,
          'source_hashes_verified': len(formal), 'original_model_files_verified': len(preserved['source_files_before']),
          'frozen_files_verified': len(preserved['frozen_before']), 'shared_build_inputs_preserved': True,
          'owned_process_groups_absent': True, 'production_preserved': True,
          'new_listeners_or_requests_created': False, 'new_asr_calls': 0, 'candidate_promoted': False,
          'limits': ['Full tensor parity fails at step84; do not generalize the earlier short-prefix parity result',
                     'Only two deliberate repeats diverge; no population failure-rate claim',
                     'Main talker uses full KVCache on both sides; decoder72-frame policy is a separate finding',
                     'No automatic or physical quality/stability gate relaxed']}
(base / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
print(json.dumps({k: v for k, v in result.items() if k not in ['pairs', 'first_numerical_differences', 'limits']}, ensure_ascii=False, indent=2))
