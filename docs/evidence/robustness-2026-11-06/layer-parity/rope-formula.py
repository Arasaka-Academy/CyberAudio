"""No model load: compare exact captured RoPE constants with CPU/GPU formulas."""
from pathlib import Path
import hashlib
import json
import time

import mlx.core as mx
import numpy as np
from mlx_audio.tts.models.qwen3_tts.talker import TalkerRotaryEmbedding

base = Path(__file__).resolve().parent
project = base.parents[2]
output = base / 'rope-formula.json'
assert not output.exists()
start = time.monotonic()
native_path = base / 'native-capture/known-failure/step-084.safetensors'
reference_path = base / 'reference-capture/known-failure/step-084.safetensors'
native, reference = mx.load(str(native_path)), mx.load(str(reference_path))
config = json.loads((project / '.context/robustness/linked-models/tts/config.json').read_text())['talker_config']
dim, theta = config['head_dim'], config['rope_theta']
results = []


def floats(a):
    return np.asarray(a.astype(mx.float32))


def metrics(a, b):
    a, b = floats(a), floats(b)
    delta = a.astype(np.float64) - b.astype(np.float64)
    return {'exact': bool(np.array_equal(a, b)), 'maximum_abs': float(np.max(np.abs(delta))),
            'different_values': int(np.count_nonzero(delta))}


for device in [mx.gpu, mx.cpu]:
    with mx.stream(device):
        e = mx.arange(0, dim, 2, dtype=mx.float32) / dim
        p = mx.power(mx.array(theta, dtype=mx.float32), e)
        array64 = mx.array(theta, dtype=mx.float32)
        candidates = {'actual_reference_class': TalkerRotaryEmbedding(dim, base=theta)._inv_freq,
                      'literal_power': 1.0 / (theta ** e), 'array_power': 1.0 / p,
                      'reciprocal_power': mx.reciprocal(p), 'negative_power': mx.power(array64, -e)}
        for name, value in candidates.items():
            mx.eval(value)
            results.append({'device': str(device), 'formula': name,
                            'native': metrics(value, native['inv_freq']), 'reference': metrics(value, reference['inv_freq']),
                            'float32_sha256': hashlib.sha256(floats(value).tobytes()).hexdigest()})
positions = [102, 103, 104]
details = []
for pos in positions:
    with mx.stream(mx.gpu):
        pairs = {}
        for name, inv in [('native', native['inv_freq']), ('reference', reference['inv_freq'])]:
            freqs = inv * mx.array(pos, dtype=mx.float32)
            pairs[name] = {'cos': mx.cos(freqs).astype(mx.bfloat16), 'sin': mx.sin(freqs).astype(mx.bfloat16)}
        details.append({'position': pos, **{name: metrics(pairs['native'][name], pairs['reference'][name]) for name in ['cos', 'sin']}})
nv, rv = floats(native['inv_freq']), floats(reference['inv_freq'])
indices = np.flatnonzero(nv != rv)
report = {'status': 'diagnostic_completed', 'head_dim': dim, 'rope_theta': theta,
          'native_snapshot_sha256': hashlib.sha256(native_path.read_bytes()).hexdigest(),
          'reference_snapshot_sha256': hashlib.sha256(reference_path.read_bytes()).hexdigest(),
          'formulas': results, 'position_controls': details,
          'inv_freq_differences': [{'index': int(i), 'native': float(nv[i]), 'reference': float(rv[i])} for i in indices],
          'elapsed_seconds': time.monotonic() - start, 'model_loaded': False,
          'limits': ['Formula controls alone do not qualify a production model change']}
output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
print(json.dumps(report, indent=2))
