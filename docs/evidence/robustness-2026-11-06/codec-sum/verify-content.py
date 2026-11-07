"""Verify every complete readback and the frozen paired content gates; no inference."""
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import urllib.request
import wave
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

AUDIT = Path(__file__).resolve().parent
ROOT = AUDIT.parent / 'codec-sum-20261107'
PROJECT = AUDIT.parents[2]
OUTPUT = ROOT / 'content-verification.private.json'
assert not OUTPUT.exists(), 'Preserve previous evidence'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def distance(a, b):
    table = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        table[i][0] = i
    for j in range(len(b) + 1):
        table[0][j] = j
    for i, x in enumerate(a, 1):
        for j, y in enumerate(b, 1):
            table[i][j] = min(table[i-1][j] + 1, table[i][j-1] + 1, table[i-1][j-1] + (x != y))
    return table[-1][-1]


plan = json.loads((ROOT / 'protocol.json').read_text())
generation = json.loads((ROOT / 'generation-verification.private.json').read_text())
assert generation['necessary_generation_gate_passed']
execution = json.loads((ROOT / 'asr-execution-protocol.json').read_text())
failed = json.loads((ROOT / 'roundtrip-http/runner.json').read_text())
assert sha(ROOT / 'roundtrip-http/runner.json') == execution['failed_attempt_sha256']
assert failed['completed_requests'] == 0 and 'owned_helper_pid' not in failed
assert failed['production_preserved'] and failed['owned_listener_closed']
runroot = ROOT / execution['output_directory']
runner = json.loads((runroot / 'runner.json').read_text())
assert runner['status'] == 'whole_test_transcribed_not_quality_accepted'
manifest = json.loads((ROOT / 'roundtrip-manifest.json').read_text())
assert manifest['protocol_sha256'] == sha(ROOT / 'protocol.json') == execution['frozen_study_protocol_sha256']
assert sha(ROOT / 'roundtrip-manifest.json') == execution['manifest_sha256']
assert runner['completed_requests'] == manifest['expected_requests'] == execution['expected_requests'] == 46
assert runner['owned_helper_exit_code'] == 0 and runner['temporary_request_count'] == 0
assert runner['study_files_sha256'] == {name: sha(ROOT / name) for name in runner['study_files_sha256']}
requests = [json.loads(line) for line in (runroot / 'results.jsonl').read_text().splitlines()]
assert [row['id'] for row in requests] == [row['id'] for row in manifest['cases']]
pcm = {}
for arm in ['baseline', 'candidate']:
    report = json.loads((ROOT / arm / 'report.json').read_text())
    assert report['status'] == 'diagnostic_completed' and [row['case'] for row in report['results']] == plan['cases']
    for row in report['results']:
        payload = (ROOT / arm / row['pcm_file']).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == row['pcm_sha256']
        pcm[(arm, row['id'])] = payload
pieces = defaultdict(list)
coverage = defaultdict(dict)
cases = {row['id']: row for row in plan['cases']}
for case, request in zip(manifest['cases'], requests, strict=True):
    assert request['http_status'] == 200 and sha(case['audio']) == case['audio_sha256']
    assert abs(request['response']['duration'] - case['frames'] / 16000) < 1e-8
    expected_language = 'auto' if case['route'] == 'auto' else {'chinese': 'zh', 'english': 'en'}[cases[case['source_id']]['language']]
    assert case['request_language'] == expected_language
    with wave.open(case['audio'], 'rb') as sound:
        assert (sound.getnchannels(), sound.getsampwidth(), sound.getframerate()) == (1, 2, 24000)
        payload = sound.readframes(sound.getnframes())
    start = case['part'] * 1152000
    assert payload == pcm[(case['arm'], case['source_id'])][start:start + 1152000]
    assert len(payload) * 2 // 3 == case['frames'] * 2 and len(payload) <= 1152000
    pieces[(case['arm'], case['source_id'], case['route'])].append((case['part'], case['parts'], request['response']['text']))
    coverage[(case['arm'], case['source_id'], case['route'])][case['part']] = payload
for key, chunks in coverage.items():
    assert b''.join(chunks[i] for i in range(len(chunks))) == pcm[key[:2]]
assert len(pieces) == 28
scorer_path = ROOT.parent / 'post-delivery-20261107/score-natural-asr.py'
assert sha(scorer_path) == runner['scorer_sha256']
spec = importlib.util.spec_from_file_location('frozen_codec_sum_scorer', scorer_path)
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)
scorer.self_test()
texts = {}
for key, parts in pieces.items():
    parts.sort()
    assert [x[0] for x in parts] == list(range(parts[0][1]))
    text = ' '.join(x[2] for x in parts)
    reference = cases[key[1]]['text']
    expected, actual = scorer.mixed_tokens(reference), scorer.mixed_tokens(text)
    edits = distance(expected, actual)
    assert edits == scorer.distance(expected, actual)
    texts[key] = {'arm': key[0], 'id': key[1], 'route': key[2], 'text': text, 'reference': reference,
        'reference_tokens': len(expected), 'edits': edits, 'normalized_exact': expected == actual}
pairs = []
for case in plan['cases']:
    for route in ['auto', 'producer']:
        before = texts[('baseline', case['id'], route)]
        after = texts[('candidate', case['id'], route)]
        pairs.append({'id': case['id'], 'route': route, 'baseline_edits': before['edits'],
            'candidate_edits': after['edits'], 'candidate_worse': after['edits'] > before['edits'],
            'candidate_exact': after['normalized_exact']})
content_gate = {
    'no_pair_worse_either_route': all(not x['candidate_worse'] for x in pairs),
    'all_known_failure_routes_exact': all(x['candidate_exact'] for x in pairs if x['id'].startswith('known-')),
}
sources = json.loads((ROOT / 'source-before.json').read_text())
assert sources == {name: sha(PROJECT / name) for name in sources}
asr_sources = json.loads((runroot / 'source-before.json').read_text())
assert asr_sources == {name: sha(PROJECT / name) for name in asr_sources}
metadata = json.loads((ROOT.parent / 'natural-reference-20261107-complete/protected-metadata-before.private.json').read_text())
assert all([Path(name).stat().st_size, Path(name).stat().st_mtime_ns] == value for name, value in metadata.items())
build = json.loads((ROOT / 'build/build.json').read_text())
assert build['protected_inputs_sha256'] == {name: sha(name) for name in build['protected_inputs_sha256']}
app = Path(execution['app'])
app_build = json.loads((app.parent / 'build.private.json').read_text())
assert sha(app.parent / 'build.private.json') == execution['build_manifest_sha256']
assert all((app / name).is_symlink() and os.readlink(app / name) == value['target'] if value['type'] == 'symlink'
           else sha(app / name) == value['sha256'] for name, value in app_build['app_files'].items())
assert all(sha(PROJECT / name) == value for name, value in app_build['source_sha256'].items())
assert app_build['frozen_sha256'] == {name: sha(PROJECT / name) for name in app_build['frozen_sha256']}
assert all(runner[key] for key in ['production_preserved', 'source_preserved', 'protected_file_metadata_preserved', 'owned_listener_closed'])
with urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=5) as response:
    ready = json.load(response)
assert ready['instance'] == runner['production_before']['instance']
for name, key in [('service.json', 'config_sha256'), ('api-token', 'token_sha256')]:
    assert sha(Path.home() / 'Library/Application Support/CyberAudio/config' / name) == runner['production_before'][key]
processes = subprocess.check_output(['/bin/ps', '-axo', 'pid=,command='], text=True).splitlines()
assert not any(str(runroot / 'service') in row for row in processes)
with socket.socket() as connection:
    assert connection.connect_ex(('127.0.0.1', plan['execution']['port'])) != 0
assert not list((runroot / 'service/tmp').glob('request-*'))
summary = {
    'status': 'fixed_codec_sum_pilot_content_verified', 'verified_at': datetime.now(timezone.utc).isoformat(),
    'protocol_sha256': sha(ROOT / 'protocol.json'), 'generation_verification_sha256': sha(ROOT / 'generation-verification.private.json'),
    'generation_gate_passed': True, 'content_gate': content_gate,
    'pilot_qualified_for_full_original_cohort': all(content_gate.values()), 'candidate_promoted': False,
    'asr_requests': len(requests), 'combined_routes': len(texts), 'paired_routes': len(pairs),
    'all14_complete_outputs_and_both_routes_verified': True,
    'zero_request_preflight_failure_preserved': True, 'inference_retries': 0,
    'source_files_verified': len(sources), 'shared_build_inputs_verified': len(build['protected_inputs_sha256']),
    'protected_metadata_verified': len(metadata), 'current_app_files_verified': len(app_build['app_files']),
    'production_preserved': True, 'owned_model_processes_absent': True, 'owned_listener_closed': True,
    'temporary_requests': 0, 'pairs': pairs, 'roundtrips': list(texts.values()),
    'limits': ['ASR readback is not human intelligibility or pronunciation acceptance.',
        'Pilot qualification only admits the unchanged original144-case cohort, with every original strict M4/physical/distribution gate still open.'],
}
OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({k:v for k,v in summary.items() if k not in ['roundtrips', 'pairs', 'limits']}, ensure_ascii=False))
print(json.dumps(pairs, ensure_ascii=False))
