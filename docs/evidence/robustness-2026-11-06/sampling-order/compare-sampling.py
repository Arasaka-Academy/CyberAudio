"""Compare observed actual sampler distributions on identical retained logits."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import inspect
import json
import time

import mlx.core as mx
import numpy as np
import mlx_audio.lm.sample_utils as sampling
from mlx_audio.tts.models.qwen3_tts import qwen3_tts as qwen

base = Path(__file__).resolve().parent
project = base.parents[2]
capture_root = base.parent / 'reference-parity-20261107/native-capture'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
plan = read(base / 'plan.json')
captures = read(capture_root / 'report.json')
native = read(base / 'native-filters/report.json')
config = read((project / '.context/robustness/linked-models/tts/config.json').resolve())['talker_config']
eos, vocab = config['codec_eos_token_id'], config['vocab_size']
suppressed = [i for i in range(vocab - 1024, vocab) if i != eos]
output = base / 'comparison.json'
assert not output.exists()
assert native['status'] == 'diagnostic_completed' and native['calls'] == 2336
assert native['source_report_sha256'] == sha(capture_root / 'report.json')
start = time.monotonic()
report = {'status': 'running', 'started': datetime.now(timezone.utc).isoformat(),
          'plan_sha256': sha(base / 'plan.json'), 'native_report_sha256': sha(base / 'native-filters/report.json'),
          'model_source_sha256': sha(inspect.getfile(qwen.Model)),
          'actual_sampling_source_sha256': sha(inspect.getfile(sampling)),
          'categorical_module': qwen.categorical_sampling.__module__,
          'scope': 'Identical native logit inputs and forced native histories; reference sample outputs do not form a free-running generation',
          'results': []}

def save():
    report['elapsed_seconds'] = time.monotonic() - start
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')

def as_float(array): return np.asarray(array.astype(mx.float32)).reshape(-1).astype(np.float64)

def probabilities(array):
    values = as_float(array)
    assert not np.isnan(values).any() and not np.isposinf(values).any()
    values = np.exp(values - np.max(values))
    return values / values.sum()

def compare(a, b):
    x, y = as_float(a), as_float(b)
    assert x.shape == y.shape
    ma, mb = np.isfinite(x), np.isfinite(y)
    both = ma & mb
    pa, pb = probabilities(a), probabilities(b)
    return {'values_exact': bool(np.array_equal(x, y)),
            'support_equal': bool(np.array_equal(ma, mb)),
            'support_difference_tokens': int(np.count_nonzero(ma != mb)),
            'native_finite': int(ma.sum()), 'reference_finite': int(mb.sum()),
            'common_finite_max_abs_logit': float(np.max(np.abs(x[both] - y[both]))) if both.any() else None,
            'maximum_probability_difference': float(np.max(np.abs(pa - pb))),
            'total_variation': float(np.sum(np.abs(pa - pb)) / 2)}

def penalty_input(logits, history, main):
    value = logits[:, -1, :]
    if main:
        ids = mx.array(suppressed, dtype=mx.int32)[None, :]
        value = mx.put_along_axis(value, ids, mx.full(ids.shape, -float('inf'), value.dtype), axis=-1)
        if history:
            ids = mx.array(list(set(history)), dtype=mx.int32)[None, :]
            selected = mx.take_along_axis(value, ids, axis=-1)
            penalized = mx.where(selected < 0, selected * 1.05, selected / 1.05)
            value = mx.put_along_axis(value, ids, penalized, axis=-1)
    return value

def native_formula(logits, history, main, restore_eos):
    value = penalty_input(logits, history, main)
    raw_eos = value[:, eos:eos+1] if main else None
    value = sampling.apply_top_k(value, 50)
    if main and restore_eos:
        value = mx.put_along_axis(value, mx.array([[eos]], dtype=mx.int32), raw_eos, axis=-1)
    return value / 0.7

original = qwen.categorical_sampling
observed = []
def spy(logits, temp):
    assert temp == 1.0
    observed.append(logits)
    return original(logits, temp)

save()
try:
    mx.set_default_device(mx.gpu)
    mx.set_cache_limit(64 * 1024 * 1024)
    for capture, native_case in zip(captures['results'], native['results'], strict=True):
        assert capture['id'] == native_case['id'] and native_case['all_codes_match']
        mx.random.seed(capture['case']['seed'])
        history = []
        result = {'id': capture['id'], 'case': capture['case'], 'steps': []}
        report['results'].append(result)
        for item, native_item in zip(capture['steps'], native_case['steps'], strict=True):
            assert time.monotonic() - start < plan['reference_budget_seconds']
            raw_path = capture_root / capture['id'] / item['file']
            filtered_path = base / 'native-filters' / capture['id'] / native_item['file']
            assert sha(raw_path) == item['sha256'] and sha(filtered_path) == native_item['sha256']
            raw, filtered = mx.load(str(raw_path)), mx.load(str(filtered_path))
            step = {'step': item['step'], 'groups': []}
            result['steps'].append(step)
            for group in range(16):
                main = group == 0
                logits = raw['logits'] if main else raw['code_logits'][:, group-1:group, :]
                native_final = filtered['main'] if main else filtered['auxiliary'][:, group-1, :]
                arguments = dict(temperature=0.7, top_k=50, top_p=1.0,
                                 repetition_penalty=1.05 if main else 1.0,
                                 generated_tokens=history if main else None,
                                 suppress_tokens=suppressed if main else None)
                if not report.get('observer_control_exact'):
                    mx.random.seed(196)
                    qwen.categorical_sampling = original
                    direct = qwen.Model._sample_token(None, logits, **arguments).item()
                    mx.random.seed(196)
                    qwen.categorical_sampling = spy
                    observed.clear()
                    wrapped = qwen.Model._sample_token(None, logits, **arguments).item()
                    assert direct == wrapped and len(observed) == 1
                    report['observer_control_exact'] = True
                    mx.random.seed(capture['case']['seed'])
                qwen.categorical_sampling = spy
                observed.clear()
                token = int(qwen.Model._sample_token(None, logits, **arguments).item())
                assert len(observed) == 1
                reference_final = observed[0]
                formula = native_formula(logits, history, main, True)
                assert compare(native_final, formula)['values_exact'], 'Native formula does not match actual source-extracted sampler'
                without_eos = native_formula(logits, history, main, False)
                metric = {'group': group, 'native_code': native_item['codes'][group], 'reference_code_on_forced_path': token,
                          'native_formula_exact': True, 'comparison': compare(native_final, reference_final),
                          'without_eos_restoration': compare(without_eos, reference_final)}
                if main:
                    metric['native_eos_probability'] = float(probabilities(native_final)[eos])
                    metric['reference_eos_probability'] = float(probabilities(reference_final)[eos])
                step['groups'].append(metric)
            history.append(native_item['codes'][0])
        save()
        print('FILTERS', capture['id'], len(result['steps']), 'steps', flush=True)
        mx.clear_cache()
    all_groups = [g for case in report['results'] for step in case['steps'] for g in step['groups']]
    for title, selected in [('all', all_groups), ('main', [g for g in all_groups if g['group'] == 0]),
                            ('auxiliary', [g for g in all_groups if g['group'] != 0])]:
        report[title + '_summary'] = {
            'vectors': len(selected), 'exact_vectors': sum(g['comparison']['values_exact'] for g in selected),
            'equal_support_vectors': sum(g['comparison']['support_equal'] for g in selected),
            'without_eos_exact_vectors': sum(g['without_eos_restoration']['values_exact'] for g in selected),
            'maximum_total_variation': max(g['comparison']['total_variation'] for g in selected),
            'maximum_total_variation_without_eos': max(g['without_eos_restoration']['total_variation'] for g in selected),
            'sampled_tokens_equal_on_forced_path': sum(g['native_code'] == g['reference_code_on_forced_path'] for g in selected)}
    report['status'] = 'diagnostic_completed'
except Exception as error:
    report.update(status='failed', error_type=type(error).__name__, error=str(error))
    raise
finally:
    qwen.categorical_sampling = original
    report['finished'] = datetime.now(timezone.utc).isoformat()
    save()
