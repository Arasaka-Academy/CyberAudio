"""Independently verify retained paired generations and preservation; no inference."""
import hashlib
import json
import socket
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

AUDIT = Path(__file__).resolve().parent
ROOT = AUDIT.parent / 'codec-sum-20261107'
PROJECT = AUDIT.parents[2]
OUTPUT = ROOT / 'generation-verification.private.json'
assert not OUTPUT.exists(), 'Preserve previous evidence'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


plan = json.loads((ROOT / 'protocol.json').read_text())
runner = json.loads((ROOT / 'runner.json').read_text())
build = json.loads((ROOT / 'build/build.json').read_text())
assert runner['status'] == 'generations_complete_not_content_accepted'
assert runner['completed_arms'] == ['baseline', 'candidate']
assert runner['protocol_sha256'] == build['plan_sha256'] == sha(ROOT / 'protocol.json')
assert build['status'] == 'built_no_model' and build['shared_inputs_preserved']
assert plan['files_sha256'] == {name: sha(ROOT / name) for name in plan['files_sha256']}
assert build['protected_inputs_sha256'] == {name: sha(name) for name in build['protected_inputs_sha256']}
delta = json.loads((ROOT / 'delta.json').read_text())
formal = Path(plan['sdk_source']).read_text()
private = (ROOT / 'build/Qwen3TTS.swift').read_text()
assert private.count(delta['after']) == 1 and private.replace(delta['after'], delta['before'], 1) == formal
assert sha(plan['sdk_source']) == plan['sdk_source_sha256']
reference_path = Path(plan['baseline_reference'])
assert sha(reference_path) == plan['baseline_reference_sha256']
references = json.loads(reference_path.read_text())['results']
assert len(references) == len(plan['cases']) == 7
eos = json.loads((Path(plan['model_directory']) / 'config.json').read_text())['talker_config']['codec_eos_token_id']
arms = {}
for arm in ['baseline', 'candidate']:
    assert sha(ROOT / 'build' / (arm + '-probe')) == build['executables_sha256'][arm]
    report = json.loads((ROOT / arm / 'report.json').read_text())
    assert report['status'] == 'diagnostic_completed' and report['plan_sha256'] == sha(ROOT / 'protocol.json')
    rows = report['results']
    assert [row['case'] for row in rows] == plan['cases'] and len(rows) == 7
    for row in rows:
        pcm = ROOT / arm / row['pcm_file']
        assert sha(pcm) == row['pcm_sha256']
        assert len(row['tokens']) == row['token_count'] <= 1200
        assert row['eos_seen'] == (row['tokens'][-1] == eos)
        assert row['pcm_bytes'] == pcm.stat().st_size == (row['token_count'] - int(row['eos_seen'])) * 3840
        assert row['audio_seconds'] == row['pcm_bytes'] / 48000
    arms[arm] = rows
assert all(row['tokens'] == ref['tokens'] and row['pcm_sha256'] == ref['pcm_sha256'] and row['case'] == ref['case'] for row, ref in zip(arms['baseline'], references, strict=True))
comparisons = []
for old, new in zip(arms['baseline'], arms['candidate'], strict=True):
    assert old['case'] == new['case']
    prefix = 0
    for a, b in zip(old['tokens'], new['tokens']):
        if a != b:
            break
        prefix += 1
    comparisons.append({
        'id': old['id'], 'baseline_tokens': old['token_count'], 'candidate_tokens': new['token_count'],
        'baseline_eos': old['eos_seen'], 'candidate_eos': new['eos_seen'],
        'baseline_seconds': old['audio_seconds'], 'candidate_seconds': new['audio_seconds'],
        'main_code_common_prefix': prefix, 'first_sample_exact': old['tokens'][0] == new['tokens'][0],
        'full_main_codes_equal': old['tokens'] == new['tokens'], 'full_pcm_equal': old['pcm_sha256'] == new['pcm_sha256'],
        'baseline_pcm_sha256': old['pcm_sha256'], 'candidate_pcm_sha256': new['pcm_sha256'],
    })
repeats = [x for x in arms['candidate'] if x['id'] in ['known-failure', 'known-failure-repeat']]
qualified = {
    'all_seven_actual_eos': all(x['candidate_eos'] for x in comparisons),
    'all_seven_within15_seconds': all(x['candidate_seconds'] <= 15 for x in comparisons),
    'candidate_repeats_exact': repeats[0]['tokens'] == repeats[1]['tokens'] and repeats[0]['pcm_sha256'] == repeats[1]['pcm_sha256'],
    'all_first_samples_unchanged': all(x['first_sample_exact'] for x in comparisons),
}
assert all(runner[key] == value for key, value in qualified.items())
necessary = all(qualified.values())
assert runner['necessary_generation_gate_passed'] == necessary
sources = json.loads((ROOT / 'source-before.json').read_text())
assert sources == {name: sha(PROJECT / name) for name in sources}
metadata = json.loads((ROOT.parent / 'natural-reference-20261107-complete/protected-metadata-before.private.json').read_text())
assert all([Path(name).stat().st_size, Path(name).stat().st_mtime_ns] == value for name, value in metadata.items())
frozen = json.loads((Path(plan['app']).parent / 'build.json').read_text())
assert frozen['frozen_sha256'] == {name: sha(PROJECT / name) for name in frozen['frozen_sha256']}
assert all(sha(Path(plan['app']) / name) == value['sha256'] for name, value in frozen['files'].items())
assert runner['production_preserved'] and runner['source_preserved'] and runner['frozen_preserved']
with urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=5) as response:
    ready = json.load(response)
assert ready['instance'] == runner['production_before']['instance']
for name, key in [('service.json', 'config_sha256'), ('api-token', 'token_sha256')]:
    assert sha(Path.home() / 'Library/Application Support/CyberAudio/config' / name) == runner['production_before'][key]
processes = subprocess.check_output(['/bin/ps', '-axo', 'pid=,command='], text=True).splitlines()
assert not any(str(ROOT / 'build' / (arm + '-probe')) + ' ' in row for arm in ['baseline', 'candidate'] for row in processes)
with socket.socket() as connection:
    assert connection.connect_ex(('127.0.0.1', plan['execution']['port'])) != 0
summary = {
    'status': 'fixed_codec_sum_pilot_generations_verified', 'verified_at': datetime.now(timezone.utc).isoformat(),
    'protocol_sha256': sha(ROOT / 'protocol.json'), 'runner_sha256': sha(ROOT / 'runner.json'),
    'fresh_baselines': 7, 'fresh_candidates': 7, 'retained_baseline_exact_matches': 7,
    'single_source_delta_verified': True, 'qualification': qualified,
    'necessary_generation_gate_passed': necessary, 'asr_expansion_allowed': necessary,
    'candidate_promoted': False, 'comparisons': comparisons,
    'source_files_verified': len(sources), 'protected_metadata_verified': len(metadata),
    'shared_build_inputs_verified': len(build['protected_inputs_sha256']),
    'frozen_artifacts_verified': len(frozen['frozen_sha256']), 'app_files_verified': len(frozen['files']),
    'production_preserved': True, 'owned_model_processes_absent': True, 'owned_listener_closed': True,
    'limits': ['Operator parity is not whole-model official generation parity or quality acceptance.',
        'Fixed diagnostic cases are not a population failure estimate.',
        'All original full-cohort, strict M4, natural-ASR, physical/listening and distribution gates retain their requirements.'],
}
OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({key:value for key,value in summary.items() if key not in ['comparisons', 'limits']}, ensure_ascii=False))
print(json.dumps(comparisons, ensure_ascii=False))
