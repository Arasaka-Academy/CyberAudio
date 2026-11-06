"""Freeze a model-free DSP study on all twelve previously observed inputs."""
from pathlib import Path
import hashlib
import json
import subprocess

base = Path(__file__).resolve().parent
project = base.parents[2]
previous = base.parent / 'asr-reference-20261107'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
assert read(previous / 'verification.json')['status'] == 'verified'
plan = read(previous / 'plan.json')
native = read(previous / 'native-front/report.json')
reference = read(previous / 'front-comparison.json')
sdk = project / '.context/icon-native-build/checkouts/mlx-audio-swift/Sources'
runtime = base.parent / 'reference-parity-20261107/runtime/lib/python3.13/site-packages'
sources = [sdk / 'MLXAudioCore/DSP.swift', sdk / 'MLXAudioSTT/Models/Qwen3ASR/Qwen3ASR.swift',
           runtime / 'transformers/audio_utils.py', runtime / 'transformers/models/whisper/feature_extraction_whisper.py']
cases = []
for case, n, r in zip(plan['cases'], native['results'], reference['results'], strict=True):
    assert case['id'] == n['id'] == r['id']
    cases.append({'id': case['id'], 'group': case['group'],
        'native_file': str(previous / 'native-front' / n['file']), 'native_sha256': n['sha256'],
        'reference_file': str(previous / 'reference-front' / r['file']), 'reference_sha256': r['sha256']})
output = {'purpose': 'Reproduce all retained Mel boundaries without model weights, then isolate CPU window/filter constants and spectrum/reduction precision; no production changes',
          'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
          'prior_verification_sha256': sha(previous / 'verification.json'), 'cases': cases,
          'protected_source_sha256': {str(p): sha(p) for p in sources},
          'native_arms': ['original-decomposition', 'reference-window-only', 'reference-filters-only', 'reference-window-and-filters'],
          'model_loaded': False, 'asr_generations': 0, 'total_workload_budget_seconds': 180,
          'snapshot_budget_bytes': 180 * 1024 * 1024,
          'limits': ['No ASR or content acceptance implied by closer numerical agreement',
                     'Float64 NumPy reference and float32 native MLX execute different precision and backend operations',
                     'Window/filter substitutions remain private and are not adopted']}
path = base / 'plan.json'
assert not path.exists()
path.write_text(json.dumps(output, indent=2) + '\n')
print('Frozen twelve-case model-free Mel study')
