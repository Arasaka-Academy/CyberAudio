"""Describe all paired generations and ASR results without promoting a candidate."""
from pathlib import Path
import hashlib
import json
import numpy as np

base = Path(__file__).resolve().parent
read = lambda p: json.loads(Path(p).read_text())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
runner = read(base / 'generation-runner.json')
asr = read(base / 'power-roundtrip/reports/asr-followup.json')
assert runner['status'] == asr['status'] == 'diagnostic_completed'
assert runner['production_preserved'] and runner['shared_inputs_preserved']
assert asr['owned_helper_exit_code'] == 0 and asr['production_and_config_preserved']
assert len(asr['results']) == asr['expected_requests'] == 28
assert all(r['http_status'] == 200 for r in asr['results'])
arms = {label: read(base / ('generation-' + label) / 'report.json') for label in ['original-jit', 'precompiled-power']}
assert all(d['failure_repeat_tokens_exact'] and d['failure_repeat_pcm_exact'] for d in arms.values())


def transcripts(group, case_id, language='auto'):
    rows = sorted([r for r in asr['results'] if r['source_id'] == group + '-' + case_id and r['asr_language'] == language], key=lambda r: r['part'])
    assert rows and [r['part'] for r in rows] == list(range(len(rows)))
    return [{'part': r['part'], 'text': r['response']['text'], 'detected_language': r['response'].get('language')} for r in rows]


def signal_stats(label, row):
    raw = base / ('generation-' + label) / row['pcm_file']
    assert sha(raw) == row['pcm_sha256']
    samples = np.fromfile(raw, dtype='<i2')
    value = samples.astype(np.float64) / 32768
    return samples, {'pcm_sha256': row['pcm_sha256'], 'rms_full_scale': float(np.sqrt(np.mean(value * value))),
                     'maximum_abs_full_scale': float(np.max(np.abs(value))), 'zero_sample_fraction': float(np.mean(samples == 0))}


pairs = []
for old, new in zip(arms['original-jit']['results'], arms['precompiled-power']['results'], strict=True):
    assert old['id'] == new['id'] and old['case'] == new['case']
    x, old_signal = signal_stats('original-jit', old)
    y, new_signal = signal_stats('precompiled-power', new)
    delta = x[:min(len(x), len(y))].astype(np.int32) - y[:min(len(x), len(y))].astype(np.int32)
    first = np.flatnonzero(delta)
    row = {'id': old['id'], 'case': old['case'],
           'baseline': {k: old[k] for k in ['token_count', 'eos_seen', 'audio_seconds']},
           'candidate': {k: new[k] for k in ['token_count', 'eos_seen', 'audio_seconds']},
           'main_tokens_exact': old['tokens'] == new['tokens'],
           'first_main_token_difference_zero_based': next((i for i, (a, b) in enumerate(zip(old['tokens'], new['tokens'])) if a != b), None),
           'pcm_exact': old['pcm_sha256'] == new['pcm_sha256'],
           'pcm_shared_prefix_samples': len(delta), 'pcm_shared_prefix_different_samples': int(np.count_nonzero(delta)),
           'pcm_shared_prefix_maximum_abs_quantization_units': int(np.max(np.abs(delta))),
           'first_pcm_difference_sample_zero_based': int(first[0]) if len(first) else None,
           'baseline_signal': old_signal, 'candidate_signal': new_signal,
           'baseline_auto_asr': transcripts('baseline', old['id']),
           'candidate_auto_asr': transcripts('candidate', new['id'])}
    row['auto_asr_results_exact'] = row['baseline_auto_asr'] == row['candidate_auto_asr']
    if 'english-numeric' in old['id']:
        row['baseline_forced_english_asr'] = transcripts('baseline', old['id'], 'en')
        row['candidate_forced_english_asr'] = transcripts('candidate', new['id'], 'en')
        row['forced_english_asr_results_exact'] = row['baseline_forced_english_asr'] == row['candidate_forced_english_asr']
    pairs.append(row)
assert all(p['auto_asr_results_exact'] for p in pairs)
assert all(p['main_tokens_exact'] for p in pairs if 'known-failure' not in p['id'])
assert all(not p['pcm_exact'] for p in pairs)
assert all(p['first_main_token_difference_zero_based'] == 251 for p in pairs if 'known-failure' in p['id'])
summary = {'status': 'diagnostic_completed', 'generation_plan_sha256': sha(base / 'generation-plan.json'),
           'generation_runner_sha256': sha(base / 'generation-runner.json'),
           'asr_report_sha256': sha(base / 'power-roundtrip/reports/asr-followup.json'),
           'generation_report_sha256': {label: sha(base / ('generation-' + label) / 'report.json') for label in arms},
           'total_generations': 12, 'paired_cases': 6, 'asr_requests': 28,
           'historical_original_prefill_token_and_pcm_controls_exact': len(runner['historical_control_parity']),
           'repeat_tokens_and_pcm_exact_each_arm': True,
           'groups': [{'group': label, 'generations': len(arm['results']),
                       'without_eos': sum(not r['eos_seen'] for r in arm['results']),
                       'maximum_audio_seconds': max(r['audio_seconds'] for r in arm['results'])} for label, arm in arms.items()],
           'pairs': pairs, 'candidate_promoted': False, 'formal_source_or_defaults_changed': False,
           'conclusion': 'JIT versus precompiled Power explains the scalar parity discrepancy but does not resolve the short-sentence content/termination defect. Seed196 still emits 84.72 seconds and all four ASR segments are empty in both candidate repetitions.',
           'limits': ['All ASR outcomes are combined TTS/ASR evidence, not human listening',
                      'Identical main tokens do not establish full auxiliary-code or decoder equality',
                      'Switching global float32 Power also affects other model components; this was a private diagnostic, not a production patch',
                      'No larger matrix or strict M4 soak is justified by this failed content control',
                      'Original quality, target runtime, public download, physical and distribution gates remain unchanged']}
path = base / 'summary.json'
assert not path.exists()
path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
print(json.dumps({k: v for k, v in summary.items() if k not in ['pairs', 'limits']}, ensure_ascii=False, indent=2))
