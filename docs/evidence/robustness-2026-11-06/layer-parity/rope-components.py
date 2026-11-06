"""Model-free decomposition of the same scalar graph across runtimes."""
from pathlib import Path
import hashlib
import json
import mlx.core as mx
import numpy as np

base = Path(__file__).resolve().parent
path = base / 'rope-components.json'
assert not path.exists()
native = mx.load(str(base / 'native-rope-formula.safetensors'))
mx.set_default_device(mx.gpu)
indices = mx.arange(0, 128, 2, dtype=mx.float32)
exponent = indices / 128
power = mx.power(mx.array(1_000_000, dtype=mx.float32), exponent)
inverse = 1.0 / power
reference = {'indices': indices, 'exponent': exponent, 'power': power, 'inverse': inverse}
output = base / 'reference-rope-formula.safetensors'
mx.save_safetensors(str(output), reference)
results = []
for name, a in reference.items():
    x, y = np.asarray(a), np.asarray(native[name])
    difference = x.astype(np.float64) - y.astype(np.float64)
    results.append({'tensor': name, 'values_exact': bool(np.array_equal(x, y)),
                    'different_values': int(np.count_nonzero(difference)),
                    'max_abs': float(np.max(np.abs(difference))),
                    'first_difference_index': int(np.flatnonzero(difference)[0]) if np.any(difference) else None})
report = {'status': 'diagnostic_completed', 'model_loaded': False,
          'native_snapshot_sha256': hashlib.sha256((base / 'native-rope-formula.safetensors').read_bytes()).hexdigest(),
          'reference_snapshot_sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'results': results,
          'limits': ['Localizes primitive output differences; does not assign their lower-level compiler or backend cause',
                     'No formula or default was changed']}
path.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
