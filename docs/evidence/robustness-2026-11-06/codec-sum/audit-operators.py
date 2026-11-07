import hashlib
import json
import platform
import struct
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parents[2]
MODEL = Path.home() / 'Library/Application Support/CyberAudio/models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit/model.safetensors'
PROTOCOL = ROOT / 'operator-protocol.json'
OUTPUT = ROOT / 'operator-report.private.json'
assert not OUTPUT.exists(), 'Preserve previous evidence'
started = time.monotonic()
torch.set_num_threads(1)


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def digest(array):
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


class SafeReader:
    def __init__(self, path):
        self.path = path
        self.handle = path.open('rb')
        length = struct.unpack('<Q', self.handle.read(8))[0]
        self.header = json.loads(self.handle.read(length))
        self.base = 8 + length

    def array(self, key):
        item = self.header[key]
        dtype = {'BF16': '<u2', 'U32': '<u4', 'I32': '<i4'}[item['dtype']]
        lo, hi = item['data_offsets']
        self.handle.seek(self.base + lo)
        data = self.handle.read(hi - lo)
        assert len(data) == hi - lo
        return np.frombuffer(data, dtype=dtype).copy().reshape(item['shape'])

    def row(self, key, index):
        item = self.header[key]
        assert item['dtype'] == 'BF16' and len(item['shape']) == 2
        assert 0 <= index < item['shape'][0]
        length = item['shape'][1] * 2
        self.handle.seek(self.base + item['data_offsets'][0] + index * length)
        data = self.handle.read(length)
        assert len(data) == length
        return np.frombuffer(data, dtype='<u2').copy()

    def close(self):
        self.handle.close()


def float64(bits):
    return (bits.astype(np.uint32) << 16).view(np.float32).astype(np.float64)


def round_bf16(values):
    # Independent binary rounding: compute BF16 spacing and round ties to even.
    # All observed operands/results are finite; subnormal spacing is 2**-133.
    values = np.asarray(values, dtype=np.float64)
    assert np.all(np.isfinite(values))
    _, exponents = np.frexp(values)
    spacing = np.ldexp(np.ones_like(values), np.maximum(exponents - 8, -133))
    rounded = np.rint(values / spacing) * spacing
    bits = rounded.astype(np.float32).view(np.uint32)
    assert np.all((bits & 0xFFFF) == 0)
    return (bits >> 16).astype(np.uint16)


def tensor(bits):
    return torch.from_numpy(bits.copy()).view(torch.bfloat16)


def bits(t):
    return t.contiguous().view(torch.uint16).numpy().copy()


protocol = json.loads(PROTOCOL.read_text())
report_path = WORKSPACE / protocol['original_report']
assert sha(report_path) == protocol['original_report_sha256']
assert sha(MODEL) == protocol['model_sha256']
original = json.loads(report_path.read_text())
assert len(original['results']) == len(protocol['cases']) == 6
model = SafeReader(MODEL)
embedding_keys = [protocol['main_embedding']] + [
    f'talker.code_predictor.model.codec_embedding.{i}.weight' for i in range(15)
]
all_rows, all_pad, all_native, all_official, all_frame_ids = [], [], [], [], []
results = []
for row, frozen in zip(original['results'], protocol['cases'], strict=True):
    assert row['id'] == frozen['id'] and len(row['steps']) == frozen['steps']
    case_root = report_path.parent / row['id']
    prepared_path = case_root / row['prepared']['file']
    assert sha(prepared_path) == frozen['prepared_sha256'] == row['prepared']['sha256']
    assert sha(case_root / row['pcm_file']) == frozen['pcm_sha256'] == row['pcm_sha256']
    prepared = SafeReader(prepared_path)
    pad = prepared.array('tts_pad_embed').reshape(-1)
    assert np.array_equal(prepared.array('trailing_text_hidden').reshape(-1), pad)
    prepared_input = prepared.array('input_embeds')
    prepared.close()
    codec_path = report_path.parent.parent / 'native-codes' / (row['id'] + '.json')
    codec_codes = json.loads(codec_path.read_text())
    case_results = []
    previous = None
    for index, item in enumerate(row['steps']):
        assert item['step'] == index
        path = case_root / item['file']
        assert sha(path) == item['sha256']
        snapshot = SafeReader(path)
        codes = snapshot.array('codes').reshape(-1)
        assert codes.tolist() == codec_codes[index]
        retained = snapshot.array('next_input_embeds').reshape(-1)
        actual_input = snapshot.array('input_embeds')
        assert np.array_equal(actual_input, prepared_input if index == 0 else previous.reshape(1, 1, -1))
        snapshot.close()
        previous = retained
        rows = np.stack([model.row(key, int(code)) for key, code in zip(embedding_keys, codes, strict=True)])
        embeddings = tensor(rows)
        native_sum = embeddings[0].clone()
        numpy_native = rows[0].copy()
        for component in range(1, 16):
            native_sum = native_sum + embeddings[component]
            numpy_native = round_bf16(float64(numpy_native) + float64(rows[component]))
        native_next = bits(native_sum + tensor(pad))
        numpy_native_next = round_bf16(float64(numpy_native) + float64(pad))
        official_sum = embeddings.sum(dim=0)
        official_next = bits(official_sum + tensor(pad))
        numpy_sum = round_bf16(float64(rows).sum(axis=0, dtype=np.float64))
        numpy_next = round_bf16(float64(numpy_sum) + float64(pad))
        delta = float64(official_next) - float64(retained)
        terminal = int(codes[0]) == 2150
        assert terminal == (index == len(row['steps']) - 1) and row['eos_seen']
        result = {
            'step': index, 'terminal_unused': terminal, 'snapshot_sha256': item['sha256'],
            'code_sha256': digest(codes), 'embedding_rows_sha256': digest(rows),
            'retained_sha256': digest(retained), 'native_sha256': digest(native_next),
            'official_sha256': digest(official_next),
            'native_exact_retained': bool(np.array_equal(native_next, retained)),
            'numpy_exact_native': bool(np.array_equal(numpy_native_next, native_next)),
            'numpy_exact_official_sum': bool(np.array_equal(numpy_sum, bits(official_sum))),
            'numpy_exact_official_next': bool(np.array_equal(numpy_next, official_next)),
            'sum_changed_elements': int(np.count_nonzero(bits(native_sum) != bits(official_sum))),
            'next_changed_elements': int(np.count_nonzero(official_next != retained)),
            'next_max_abs_delta': float(np.max(np.abs(delta))),
            'next_rms_delta': float(np.sqrt(np.mean(delta**2))),
            'next_relative_l2_delta': float(np.linalg.norm(delta) / np.linalg.norm(float64(retained))),
        }
        case_results.append(result)
        all_rows.append(rows)
        all_pad.append(pad)
        all_native.append(retained)
        all_official.append(official_next)
        all_frame_ids.append(f'{row["id"]}:{index}')
    case_result = {
        'id': row['id'], 'case': row['case'], 'steps': len(case_results),
        'feedback_frames': sum(not x['terminal_unused'] for x in case_results),
        'changed_frames': sum(x['next_changed_elements'] > 0 for x in case_results),
        'changed_feedback_frames': sum(x['next_changed_elements'] > 0 and not x['terminal_unused'] for x in case_results),
        'changed_elements': sum(x['next_changed_elements'] for x in case_results),
        'max_abs_delta': max(x['next_max_abs_delta'] for x in case_results),
        'frames': case_results,
    }
    results.append(case_result)
    print(json.dumps({k:v for k,v in case_result.items() if k not in ('case', 'frames')}), flush=True)
model.close()
frames = [frame for row in results for frame in row['frames']]
verified = all(all(frame[key] for key in ['native_exact_retained', 'numpy_exact_native', 'numpy_exact_official_sum', 'numpy_exact_official_next']) for frame in frames)
arrays_path = ROOT / 'operator-inputs.private.npz'
np.savez(arrays_path, embedding_rows=np.stack(all_rows), pads=np.stack(all_pad), native_next=np.stack(all_native), official_next=np.stack(all_official), frame_ids=np.array(all_frame_ids))
output = {
    'finished_at': datetime.now(timezone.utc).isoformat(), 'elapsed_seconds': time.monotonic() - started,
    'scope': 'Existing complete trajectories from rejected sampling-order candidate; no new model generations. Native and MLX Python share embedding-construction code.',
    'protocol_sha256': sha(PROTOCOL), 'report_sha256': sha(report_path), 'model_sha256': protocol['model_sha256'],
    'versions': {'torch': torch.__version__, 'numpy': np.__version__, 'python': platform.python_version(), 'machine': platform.machine(), 'device': 'cpu', 'threads': torch.get_num_threads()},
    'status': 'verified' if verified else 'reconstruction_failed',
    'frame_count': len(frames), 'feedback_frames': sum(not x['terminal_unused'] for x in frames),
    'changed_frames': sum(x['next_changed_elements'] > 0 for x in frames),
    'changed_feedback_frames': sum(x['next_changed_elements'] > 0 and not x['terminal_unused'] for x in frames),
    'changed_elements': sum(x['next_changed_elements'] for x in frames),
    'all_native_reconstructions_exact': all(x['native_exact_retained'] for x in frames),
    'all_independent_numpy_comparisons_exact': all(x['numpy_exact_native'] and x['numpy_exact_official_sum'] and x['numpy_exact_official_next'] for x in frames),
    'arrays_file': arrays_path.name, 'arrays_sha256': sha(arrays_path),
    'formal_source_changed': False, 'candidate_promoted': False, 'new_model_generations': 0,
    'results': results,
}
OUTPUT.write_text(json.dumps(output, indent=2, ensure_ascii=False) + '\n')
print(json.dumps({k:v for k,v in output.items() if k != 'results'}, ensure_ascii=False), flush=True)
assert verified, 'Preserved full operator report; reconstruction failed'
