"""Observe original reference boundaries, with a full unobserved replay control."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import importlib.metadata
import inspect
import json
import textwrap
import time

import mlx.core as mx
import numpy as np
from mlx_audio.tts.utils import load
from mlx_audio.tts.models.qwen3_tts import talker as t

base = Path(__file__).resolve().parent
project = base.parents[2]
read = lambda p: json.loads(Path(p).read_text())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
plan = read(base / 'plan.json')
native = read(base / 'native-capture/report.json')
capture_path = Path(plan['capture_report'])
capture = read(capture_path)
previous = read(base.parent / 'complete-codec-20261107/reference-comparison.json')
assert native['status'] == 'diagnostic_completed' and all(r['all_original_native_forward_exact'] for r in native['results'])
assert sha(capture_path) == plan['capture_report_sha256']
assert importlib.metadata.version('mlx') == plan['python_mlx']
assert importlib.metadata.version('mlx-audio') == plan['python_mlx_audio']
output = base / 'reference-comparison.json'
assert not output.exists()
started = time.monotonic()
report = {'status': 'running', 'started': datetime.now(timezone.utc).isoformat(),
          'plan_sha256': sha(base / 'plan.json'), 'native_report_sha256': sha(base / 'native-capture/report.json'),
          'sampling': 'None; captured input embeddings are replayed', 'results': []}


def save():
    report['elapsed_seconds'] = time.monotonic() - started
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def values(a):
    return np.asarray(a.astype(mx.float32))


def digest(a):
    return hashlib.sha256(values(a).tobytes()).hexdigest()


def compare(a, b):
    x, y = values(a), values(b)
    assert np.isfinite(x).all() and np.isfinite(y).all()
    d = x.astype(np.float64) - y.astype(np.float64)
    return {'reference_shape': list(a.shape), 'native_shape': list(b.shape),
            'reference_dtype': str(a.dtype), 'native_dtype': str(b.dtype),
            'shape_equal': a.shape == b.shape, 'dtype_equal': a.dtype == b.dtype,
            'values_exact': bool(np.array_equal(x, y)), 'max_abs': float(np.max(np.abs(d))),
            'mean_abs': float(np.mean(np.abs(d))), 'rms_abs': float(np.sqrt(np.mean(d * d)))}


active = None
layer_index = 0
layer_ids = {}


def attn(name, value):
    if active is not None:
        active[f'layer-{layer_index:02d}.attn.{name}'] = value


def instrument_attention():
    source = textwrap.dedent(inspect.getsource(t.TalkerAttention.__call__))
    changed = source.replace('def __call__(', 'def diagnostic_attention(', 1)

    def replace(before, after):
        nonlocal changed
        assert changed.count(before) == 1, before
        changed = changed.replace(before, after)

    replace('    # Apply QK normalization', '''    attn("input", x)
    attn("q_raw", q)
    attn("k_raw", k)
    attn("v_raw", v)
    # Apply QK normalization''')
    replace('    k = self.k_norm(k)', '''    k = self.k_norm(k)
    attn("q_norm", q)
    attn("k_norm", k)''')
    replace('    q, k = apply_multimodal_rotary_pos_emb(q, k, cos, sin)', '''    q, k = apply_multimodal_rotary_pos_emb(q, k, cos, sin)
    attn("q_rotary", q)
    attn("k_rotary", k)''')
    replace('    # Use fast scaled dot product attention', '''    attn("k_cache", k)
    attn("v_cache", v)
    # Use fast scaled dot product attention''')
    replace('    # Reshape back', '''    attn("sdpa", output)
    # Reshape back''')
    replace('    return self.o_proj(output)', '''    projected = self.o_proj(output)
    attn("projected", projected)
    return projected''')
    (base / 'observed-reference-attention.py').write_text(changed)
    namespace = {**vars(t), 'attn': attn}
    exec(compile(changed, '<private-reference-observation>', 'exec'), namespace)
    return namespace['diagnostic_attention']


original_attention = t.TalkerAttention.__call__
original_layer = t.TalkerDecoderLayer.__call__
original_rotary = t.TalkerRotaryEmbedding.__call__


def observed_layer(self, x, *args, **kwargs):
    global layer_index
    layer_index = layer_ids[id(self)]
    if active is not None:
        active[f'layer-{layer_index:02d}.input'] = x
    result = original_layer(self, x, *args, **kwargs)
    if active is not None:
        active[f'layer-{layer_index:02d}.output'] = result
    return result


def observed_rotary(self, x, position_ids):
    result = original_rotary(self, x, position_ids)
    if active is not None:
        active.update(position_ids=position_ids, inv_freq=self._inv_freq, cos=result[0], sin=result[1])
    return result


save()
try:
    mx.set_default_device(mx.gpu)
    mx.set_cache_limit(512 * 1024 * 1024)
    model = load((project / '.context/robustness/linked-models/tts').resolve(strict=True), lazy=False, strict=True)
    report['reference_source_sha256'] = sha(inspect.getfile(type(model)))
    report['reference_talker_source_sha256'] = sha(inspect.getfile(t))
    assert report['reference_source_sha256'] == plan['python_model_source_sha256']
    layer_ids = {id(layer): i for i, layer in enumerate(model.talker.model.layers)}
    diagnostic_attention = instrument_attention()
    all_compares = []
    for case, native_row in zip(plan['cases'], native['results'], strict=True):
        case_id = case['id']
        old = next(r for r in capture['results'] if r['id'] == case_id)
        previous_row = next(r for r in previous['results'] if r['id'] == case_id)
        expected = []
        for meta in old['steps']:
            path = capture_path.parent / case_id / meta['file']
            assert sha(path) == meta['sha256']
            expected.append(mx.load(str(path)))
        row = {'id': case_id, 'case': case, 'unobserved': [], 'observed': [], 'snapshots': []}
        report['results'].append(row)
        cache = model.talker.make_cache()
        # Original implementation first; no boundary observers installed.
        assert t.TalkerAttention.__call__ is original_attention
        for index, arrays in enumerate(expected):
            logits, hidden = model.talker(arrays['input_embeds'], cache=cache)
            mx.eval(logits, hidden)
            a, b = logits[:, -1:, :], hidden[:, -1:, :]
            comparison = {'logits': compare(a, arrays['logits']), 'hidden': compare(b, arrays['hidden'])}
            assert comparison == {k: previous_row['steps'][index]['comparisons'][k] for k in comparison}, 'Replay changed earlier reference forward result'
            row['unobserved'].append({'step': index, 'logits_float32_sha256': digest(a), 'hidden_float32_sha256': digest(b), 'comparisons': comparison})
        del cache
        mx.clear_cache()
        t.TalkerAttention.__call__ = diagnostic_attention
        t.TalkerDecoderLayer.__call__ = observed_layer
        t.TalkerRotaryEmbedding.__call__ = observed_rotary
        try:
            cache = model.talker.make_cache()
            directory = base / 'reference-capture' / case_id
            directory.mkdir(parents=True, exist_ok=False)
            for index, arrays in enumerate(expected):
                assert time.monotonic() - started < plan['reference_total_budget_seconds']
                active = {} if index in plan['trace_steps'][case_id] else None
                logits, hidden = model.talker(arrays['input_embeds'], cache=cache)
                mx.eval(logits, hidden)
                control = row['unobserved'][index]
                same = digest(logits[:, -1:, :]) == control['logits_float32_sha256'] and digest(hidden[:, -1:, :]) == control['hidden_float32_sha256']
                row['observed'].append({'step': index, 'unobserved_forward_exact': same})
                assert same, 'Observation changed reference output'
                if active is not None:
                    native_meta = next(s for s in native_row['snapshots'] if s['step'] == index)
                    path = base / 'native-capture' / case_id / native_meta['file']
                    assert sha(path) == native_meta['sha256']
                    native_arrays = mx.load(str(path))
                    assert set(active) == set(native_arrays)
                    observed_path = directory / native_meta['file']
                    mx.save_safetensors(str(observed_path), active)
                    comparisons = {name: compare(value, native_arrays[name]) for name, value in active.items()}
                    ordered = ['position_ids', 'inv_freq', 'cos', 'sin']
                    for i in range(len(model.talker.model.layers)):
                        ordered.extend([f'layer-{i:02d}.input', *[f'layer-{i:02d}.attn.{n}' for n in ['input', 'q_raw', 'k_raw', 'v_raw', 'q_norm', 'k_norm', 'q_rotary', 'k_rotary', 'k_cache', 'v_cache', 'sdpa', 'projected']], f'layer-{i:02d}.output'])
                    assert set(ordered) == set(comparisons)
                    differences = [name for name in ordered if not comparisons[name]['values_exact']]
                    snapshot = {'step': index, 'file': native_meta['file'], 'bytes': observed_path.stat().st_size,
                                'sha256': sha(observed_path), 'native_sha256': sha(path), 'comparisons': comparisons,
                                'first_nonexact_boundary': differences[0] if differences else None,
                                'nonexact_boundaries': len(differences)}
                    row['snapshots'].append(snapshot)
                    all_compares.extend({'case': case_id, 'step': index, 'tensor': name, **value} for name, value in comparisons.items())
                    print('BOUNDARY', case_id, index, snapshot['first_nonexact_boundary'], len(differences), flush=True)
                active = None
            row['all_observer_controls_exact'] = all(s['unobserved_forward_exact'] for s in row['observed'])
            del cache
            mx.clear_cache()
        finally:
            active = None
            t.TalkerAttention.__call__ = original_attention
            t.TalkerDecoderLayer.__call__ = original_layer
            t.TalkerRotaryEmbedding.__call__ = original_rotary
        save()
    report['boundary_comparisons'] = len(all_compares)
    report['exact_boundary_comparisons'] = sum(c['values_exact'] for c in all_compares)
    report['status'] = 'diagnostic_completed'
except Exception as error:
    report.update(status='failed', error_type=type(error).__name__, error=str(error))
    raise
finally:
    report['finished'] = datetime.now(timezone.utc).isoformat()
    save()
