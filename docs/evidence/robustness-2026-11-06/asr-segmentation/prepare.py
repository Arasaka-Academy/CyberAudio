"""Freeze one audio-only segmentation pilot before any new ASR output."""
from pathlib import Path
import hashlib
import json
import math
import subprocess
import wave

import numpy as np

base = Path(__file__).resolve().parent
project = base.parents[2]
previous = base.parent / 'asr-reference-20261107'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
prior_plan = read(previous / 'plan.json')
assert read(base.parent / 'asr-mel-20261107/verification.json')['status'] == 'verified'
settings = {'sample_rate': 16000, 'window_ms': 20, 'hop_ms': 10, 'maximum_chunk_seconds': 18,
            'minimum_chunk_seconds': 4, 'search_from_seconds': 8,
            'minimum_silence_ms': 200, 'silence_rms_floor_dbfs': -42,
            'silence_rms_fraction_of_95th_percentile': 0.1,
            'selection': 'longest low-RMS run in the fixed search interval; earlier run breaks ties',
            'fallback': 'lowest RMS window in last three seconds before maximum length',
            'overlap_samples': 0, 'audio_removed_samples': 0, 'merge': 'join all chunk texts with one space; no deduplication or text rewriting'}


def boundaries(values):
    total = len(values)
    width, step = 320, 160
    frames = np.lib.stride_tricks.sliding_window_view(values, width)[::step]
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    threshold = max(10 ** (-42 / 20), float(np.percentile(rms, 95)) * .1)
    quiet = rms <= threshold
    starts = np.flatnonzero(quiet & ~np.r_[False, quiet[:-1]])
    ends = np.flatnonzero(quiet & ~np.r_[quiet[1:], False]) + 1
    runs = [(int(a * step), int((b - 1) * step + width)) for a, b in zip(starts, ends, strict=True)]
    output, details, start = [0], [], 0
    while total - start > 18 * 16000:
        lower, upper = start + 8 * 16000, min(start + 18 * 16000, total - 4 * 16000)
        candidates = [(a, b) for a, b in runs if b - a >= 3200 and lower <= (a + b) // 2 <= upper]
        if candidates:
            a, b = max(candidates, key=lambda p: (p[1] - p[0], -p[0]))
            cut = (a + b) // 2
            details.append({'sample': cut, 'kind': 'low-rms-run', 'run_start_sample': a, 'run_end_sample': b})
        else:
            centers = np.arange(len(rms)) * step + width // 2
            eligible = np.flatnonzero((centers >= max(lower, upper - 3 * 16000)) & (centers <= upper))
            assert len(eligible)
            selected = int(eligible[np.argmin(rms[eligible])])
            cut = int(centers[selected])
            details.append({'sample': cut, 'kind': 'lowest-rms-fallback', 'window_rms': float(rms[selected])})
        assert 4 * 16000 <= cut - start <= 18 * 16000
        output.append(cut); start = cut
    output.append(total)
    assert all(a < b for a, b in zip(output, output[1:]))
    return output, threshold, details


root = base / 'audio'; root.mkdir(exist_ok=False)
cases, expanded = [], []
for source in prior_plan['cases']:
    path = Path(source['audio'])
    assert sha(path) == source['audio_sha256']
    with wave.open(str(path), 'rb') as f:
        assert (f.getnchannels(), f.getsampwidth(), f.getframerate()) == (1, 2, 16000)
        pcm = f.readframes(f.getnframes())
    assert hashlib.sha256(pcm).hexdigest() == source['pcm_sha256']
    values = np.frombuffer(pcm, dtype='<i2').astype(np.float64) / 32768
    cuts, threshold, reasons = boundaries(values)
    baseline_id = 'baseline-' + source['id']
    expanded.append(dict(source, id=baseline_id, original_id=source['id'], arm='baseline', part=0))
    chunks = []
    if len(cuts) == 2:
        chunks.append({'request_id': baseline_id, 'reused_original_request': True, 'start_sample': 0, 'end_sample': len(values),
                       'audio': source['audio'], 'audio_sha256': source['audio_sha256'], 'pcm_sha256': source['pcm_sha256']})
    else:
        for i, (start, end) in enumerate(zip(cuts, cuts[1:])):
            chunk_id = 'segment-' + source['id'] + '-' + str(i)
            audio = root / (chunk_id + '.wav')
            payload = pcm[start * 2:end * 2]
            with wave.open(str(audio), 'wb') as f:
                f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(payload)
            row = {'id': chunk_id, 'original_id': source['id'], 'arm': 'segmented', 'part': i,
                   'audio': str(audio), 'audio_sha256': sha(audio), 'pcm_sha256': hashlib.sha256(payload).hexdigest(),
                   'duration_seconds': (end - start) / 16000, 'reference': '', 'language': None}
            expanded.append(row)
            chunks.append({'request_id': chunk_id, 'reused_original_request': False, 'start_sample': start, 'end_sample': end,
                           'audio': str(audio), 'audio_sha256': row['audio_sha256'], 'pcm_sha256': row['pcm_sha256']})
    cases.append({'source': source, 'baseline_request_id': baseline_id, 'threshold_rms': threshold,
                  'boundaries_samples': cuts, 'boundary_reasons': reasons, 'chunks': chunks})
plan = {'purpose': 'One fixed audio-only segmentation pilot on all twelve retained inputs; no SDK/model/default changes or transcript-informed boundaries',
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'source_sha256': sha(Path(__file__)), 'source_plan_sha256': sha(previous / 'plan.json'),
        'baseline_report_sha256': sha(previous / 'native-http/reports/asr-followup.json'),
        'settings': settings, 'source_cases': cases, 'cases': expanded,
        'expected_requests': len(expanded), 'max_workload_seconds': 600,
        'helper_sha256': prior_plan['helper_sha256'],
        'predeclared_gate': ['All twelve fresh baseline texts must exactly reproduce previous final-App outputs',
            'No per-case mixed-unit edit distance against retained reference may increase',
            'All eight segmented mixed texts must contain both Han characters and Latin words; this is necessary but not correctness acceptance',
            'At least one previously omitted language section must return with lower edit distance',
            'Only after all pilot conditions pass consider broader controlled/long-monolingual tests; no parameter retuning on this pilot'],
        'score': 'NFKC/casefold; each Han character is one unit, other consecutive alphanumeric characters form one unit; Levenshtein, no numeric semantic normalization',
        'limits': ['Controlled concatenation remains distinct from natural code-switching and physical speech',
            'Chunking can lose context, split words, duplicate or hallucinate text; edits and language scripts are incomplete quality measures',
            'No candidate adoption, strict-soak rerun or original gate waiver']}
output = base / 'plan.json'
assert not output.exists()
output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
print('Fixed',len(cases),'source cases;',len(expanded),'requests')
for row in cases: print(row['source']['id'],[round(s / 16000,3) for s in row['boundaries_samples']],round(row['threshold_rms'],6))
