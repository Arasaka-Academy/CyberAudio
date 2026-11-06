"""Summarize shared model behavior and the separate numerical sensitivity."""
from pathlib import Path
import hashlib
import json

base = Path(__file__).resolve().parent
read = lambda p: json.loads(Path(p).read_text())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
plan = read(base / 'plan.json')
native = read(base / 'native-http/reports/asr-followup.json')
reference = read(base / 'reference.json')
front = read(base / 'front-comparison.json')
injection = read(base / 'injection.json')
assert all(r['status'] == 'diagnostic_completed' for r in [native, reference, front, injection])
pairs = []
for case, nr, rr, ir in zip(plan['cases'], native['results'], reference['results'], injection['results'], strict=True):
    assert case['id'] == nr['id'] == rr['id'] == ir['id']
    old, new = nr['response']['text'], rr['response']['text']
    pairs.append({'id': case['id'], 'group': case['group'], 'reference': case['reference'],
                  'native_text': old, 'python_text': new, 'text_exact': old == new,
                  'native_language': nr['response'].get('language'), 'python_language': rr['response']['language'],
                  'injection_texts': {a['arm']: a['response']['text'] for a in ir['arms']},
                  'injection_matches_native': {a['arm']: a['native_text_exact'] for a in ir['arms']}})
assert sum(p['text_exact'] for p in pairs) == 10
assert all(p['text_exact'] for p in pairs if p['group'] == 'monolingual-control')
for tensor in ['audio', 'mask', 'prompt']:
    assert all(r['comparisons'][tensor]['values_exact'] for r in front['results'])
assert all(a['original_reference_text_exact'] for r in injection['results'] for a in r['arms'] if a['arm'] == 'unmodified-repeat')
assert all(a['native_text_exact'] for r in injection['results'] for a in r['arms'] if a['arm'] == 'native-encoded-only')
mel_matches = sum(a['native_text_exact'] for r in injection['results'] for a in r['arms'] if a['arm'] == 'native-mel-only')
assert mel_matches == 11
summary = {'status': 'diagnostic_completed', 'plan_sha256': sha(base / 'plan.json'),
           'input_cases': 12, 'retained_mixed_inputs': 8, 'monolingual_controls': 4,
           'actual_asr_generations': 84, 'native_http_requests': 12, 'native_direct_text_repetitions': 12,
           'python_original_generations': 12, 'python_observation_control_repetitions': 12,
           'python_causal_control_generations': 36,
           'native_python_exact_texts': 10, 'mixed_exact_texts': 6, 'monolingual_exact_texts': 4,
           'front_snapshot_comparisons': 60, 'audio_mask_and_prompt_values_exact_all_cases': True,
           'maximum_mel_abs_difference': max(r['comparisons']['mel']['max_abs'] for r in front['results']),
           'maximum_encoded_abs_difference': max(r['comparisons']['encoded']['max_abs'] for r in front['results']),
           'native_mel_injection_matches_native_text_cases': mel_matches,
           'native_encoded_injection_matches_native_text_cases': 12,
           'source_report_sha256': {name: sha(base / name) for name in ['native-http/reports/asr-followup.json', 'reference.json', 'native-front/report.json', 'front-comparison.json', 'injection.json']},
           'pairs': pairs,
           'observations': [
               'Both unmodified paths omit the Chinese section for the first clean Chinese-then-English bridge composite',
               'Both unmodified paths translate the Chinese section into English for the two clean English-then-Chinese composites',
               'Python preserves Chinese in the noisy Chinese-then-English bridge case while native omits it',
               'Replacing only Python Mel values with native Mel values reproduces that noisy-case omission',
               'Replacing only Python encoded audio with native encoded audio reproduces all twelve native texts',
               'Small numeric differences and shared reference content errors are separate observed contributors; no sole model/integration attribution'],
           'formal_source_or_defaults_changed': False, 'candidate_promoted': False,
           'limits': ['Controlled concatenation, equal RMS and synthetic Gaussian noise are not natural code-switch or microphone acceptance',
                      'Reference agreement and character/script presence do not establish transcription correctness',
                      'The exact lower-level sources of Mel/encoder numeric differences remain unassigned',
                      'No new quality, strict M4 workload, public download, physical or distribution gate accepted']}
path = base / 'summary.json'
assert not path.exists()
path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
print(json.dumps({k: v for k, v in summary.items() if k not in ['pairs', 'limits', 'source_report_sha256']}, ensure_ascii=False, indent=2))
