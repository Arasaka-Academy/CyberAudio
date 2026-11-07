import hashlib
import importlib.metadata
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

import mlx.core as mx
import numpy as np

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'mlx-operator-report.private.json'
assert not OUTPUT.exists(), 'Preserve previous evidence'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
report_path = ROOT / 'operator-report.private.json'
report = json.loads(report_path.read_text())
arrays_path = ROOT / report['arrays_file']
assert sha(arrays_path) == report['arrays_sha256'] and report['status'] == 'verified'
data = np.load(arrays_path, allow_pickle=False)
started = time.monotonic()
results = []


def from_bits(value):
    return mx.array(value, dtype=mx.uint16).view(mx.bfloat16)


def to_bits(value):
    mx.eval(value)
    floats = np.array(value.astype(mx.float32)).reshape(-1)
    bits = floats.view(np.uint32)
    assert np.all((bits & 0xFFFF) == 0)
    return (bits >> 16).astype(np.uint16)


for device_name, device in [('cpu', mx.cpu), ('gpu', mx.gpu)]:
    frames = []
    with mx.stream(device):
        for index, frame_id in enumerate(data['frame_ids']):
            embeddings = from_bits(data['embedding_rows'][index]).reshape(1, 16, 2048)
            pad = from_bits(data['pads'][index]).reshape(1, 1, 2048)
            sequential = embeddings[:, 0:1, :]
            for group in range(1, 16):
                sequential = sequential + embeddings[:, group:group + 1, :]
            direct = mx.sum(embeddings, axis=1, keepdims=True)
            promoted = mx.sum(embeddings.astype(mx.float32), axis=1, keepdims=True).astype(mx.bfloat16)
            native = to_bits(pad + sequential)
            bf16_sum = to_bits(pad + direct)
            f32_sum = to_bits(pad + promoted)
            official = data['official_next'][index]
            frames.append({
                'frame_id': str(frame_id),
                'sequential_exact_native': bool(np.array_equal(native, data['native_next'][index])),
                'bf16_sum_exact_official': bool(np.array_equal(bf16_sum, official)),
                'fp32_sum_exact_official': bool(np.array_equal(f32_sum, official)),
                'bf16_sum_mismatched_elements': int(np.count_nonzero(bf16_sum != official)),
                'fp32_sum_mismatched_elements': int(np.count_nonzero(f32_sum != official)),
                'sequential_sha256': hashlib.sha256(native.tobytes()).hexdigest(),
                'bf16_sum_sha256': hashlib.sha256(bf16_sum.tobytes()).hexdigest(),
                'fp32_sum_sha256': hashlib.sha256(f32_sum.tobytes()).hexdigest(),
            })
    row = {
        'device': device_name,
        'frames': len(frames),
        'sequential_exact_native_frames': sum(x['sequential_exact_native'] for x in frames),
        'bf16_sum_exact_official_frames': sum(x['bf16_sum_exact_official'] for x in frames),
        'fp32_sum_exact_official_frames': sum(x['fp32_sum_exact_official'] for x in frames),
        'results': frames,
    }
    results.append(row)
    print(json.dumps({k:v for k,v in row.items() if k != 'results'}), flush=True)
output = {
    'finished_at': datetime.now(timezone.utc).isoformat(),
    'elapsed_seconds': time.monotonic() - started,
    'operator_report_sha256': sha(report_path), 'arrays_sha256': sha(arrays_path),
    'versions': {'mlx': importlib.metadata.version('mlx'), 'numpy': np.__version__, 'python': platform.python_version()},
    'frozen_scope': 'Every one of the existing367 complete frames, CPU and GPU; sequential BF16, BF16 reduction, explicit FP32 reduction then BF16; same BF16 pad addition. No model generation.',
    'formal_source_changed': False, 'candidate_promoted': False, 'results': results,
}
OUTPUT.write_text(json.dumps(output, indent=2) + '\n')
print(json.dumps({k:v for k,v in output.items() if k != 'results'}), flush=True)
assert all(row['sequential_exact_native_frames'] == row['frames'] for row in results)
