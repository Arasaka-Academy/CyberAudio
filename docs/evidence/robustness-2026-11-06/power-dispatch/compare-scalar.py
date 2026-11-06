"""File-only validation of one private dispatch change and scalar outputs."""
from pathlib import Path
import hashlib
import json
import struct
import numpy as np

base = Path(__file__).resolve().parent
prior = base.parent / 'layer-parity-20261107'
read = lambda p: json.loads(Path(p).read_text())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()


def arrays(path):
    data = Path(path).read_bytes()
    n = struct.unpack('<Q', data[:8])[0]
    header = json.loads(data[8:8 + n])
    assert header.pop('__metadata__', None) is None
    result = {}
    for name, value in header.items():
        assert value['dtype'] == 'F32'
        a, b = value['data_offsets']
        value_array = np.frombuffer(data[8 + n + a:8 + n + b], dtype='<f4').reshape(value['shape'])
        assert np.isfinite(value_array).all()
        result[name] = value_array
    return result


runner = read(base / 'runner.json')
plan = read(base / 'plan.json')
assert runner['status'] == 'diagnostic_completed' and runner['shared_inputs_preserved'] and runner['production_preserved']
assert len(runner['steps']) == 5 and all(s['status'] == 'completed' and s['exit_code'] == 0 for s in runner['steps'])
assert sha(base / 'plan.json') == runner['plan_sha256']
assert sha(base / 'native-build/jit_kernels.cpp') == plan['private_source_sha256']
source = next(Path(p) for p in plan['preserved_input_sha256'] if p.endswith('/metal/jit_kernels.cpp'))
before = source.read_text()
injection = '''  // PRIVATE DIAGNOSTIC: only float32 Power dispatch uses the shipped library.
  if (std::string(op) == "Power" && in_type == float32 && out_type == float32) {
    return d.get_kernel(kernel_name);
  }
'''
after = (base / 'native-build/jit_kernels.cpp').read_text()
assert after.count(injection) == 1 and after.replace(injection, '') == before
outputs = {label: arrays(base / (label + '.safetensors')) for label in ['original-jit', 'precompiled-power']}
old = arrays(prior / 'native-rope-formula.safetensors')
reference = arrays(prior / 'reference-rope-formula.safetensors')
assert set(outputs['original-jit']) == set(outputs['precompiled-power']) == set(old) == set(reference)
results = []
for name in old:
    a, b = outputs['original-jit'][name], outputs['precompiled-power'][name]
    assert np.array_equal(a, old[name]) and np.array_equal(b, reference[name])
    delta = a.astype(np.float64) - b.astype(np.float64)
    results.append({'tensor': name, 'shape': list(a.shape), 'original_jit_matches_prior_native': True,
                    'precompiled_power_matches_prior_python': True,
                    'different_values': int(np.count_nonzero(delta)), 'max_abs': float(np.max(np.abs(delta))),
                    'original_float32_sha256': hashlib.sha256(a.tobytes()).hexdigest(),
                    'precompiled_float32_sha256': hashlib.sha256(b.tobytes()).hexdigest()})
assert {r['tensor']: r['different_values'] for r in results} == {'indices': 0, 'exponent': 0, 'inverse': 42, 'power': 46}
disassembly = read(base / 'reference-disassembly-receipt.json')
assert sha(base / 'reference-binary-disassembly.txt') == disassembly['full_sha256']
assert (base / 'reference-binary-disassembly.txt').stat().st_size == disassembly['full_bytes']
selected = base / 'reference-power-dispatch.txt'
assert sha(selected) == disassembly['selected_sha256'] and selected.stat().st_size == disassembly['selected_bytes']
assert 'Device10get_kernel' in selected.read_text() and 'get_library' not in selected.read_text()
result = {'status': 'verified', 'model_loaded': False, 'only_private_float32_power_dispatch_changed': True,
          'runner_sha256': sha(base / 'runner.json'), 'plan_sha256': sha(base / 'plan.json'),
          'output_file_sha256': {label: sha(base / (label + '.safetensors')) for label in outputs},
          'prior_native_formula_sha256': sha(prior / 'native-rope-formula.safetensors'),
          'prior_python_formula_sha256': sha(prior / 'reference-rope-formula.safetensors'),
          'results': results, 'python_binary_direct_precompiled_dispatch_observed': True,
          'full_disassembly_retained_privately': disassembly,
          'limits': ['This isolates JIT versus precompiled Power dispatch on this native runtime and shader',
                     'The exact Metal compiler transformation causing the rounding difference remains unassigned',
                     'Native and Python package/version labels do not prove equal backend builds',
                     'This model-free control does not establish content, termination or human listening quality']}
path = base / 'scalar-comparison.json'
assert not path.exists()
path.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
