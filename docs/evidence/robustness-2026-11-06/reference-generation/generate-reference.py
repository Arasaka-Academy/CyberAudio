"""Actual reference entry point, without sampler/decoder formula substitutions."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import importlib.metadata
import inspect
import json
import time

import mlx.core as mx
import numpy as np
from mlx_audio.tts.utils import load
from mlx_audio.tts.models.qwen3_tts import qwen3_tts as qwen
import mlx_audio.lm.sample_utils as sampling

base = Path(__file__).resolve().parent
project = base.parents[2]
read = lambda p: json.loads(Path(p).read_text())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
plan = read(base / 'plan.json')
output = base / 'report.json'
assert not output.exists()
for name, version in plan['versions'].items():
    assert importlib.metadata.version(name) == version
for path, digest in plan['source_sha256'].items():
    assert sha(project / path) == digest
began = time.monotonic()
report = {'status': 'running', 'started': datetime.now(timezone.utc).isoformat(),
          'plan_sha256': sha(base / 'plan.json'), 'source_sha256': sha(Path(__file__)),
          'sampling_parameters': {k: plan[k] for k in ['temperature', 'top_k', 'top_p', 'repetition_penalty', 'max_tokens', 'streaming_interval']},
          'decoder_results': [], 'results': [], 'default_changed': False, 'candidate_proposed': False}


def save():
    report['elapsed_seconds'] = time.monotonic() - began
    temporary = output.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temporary.replace(output)


def pcm16(audio):
    values = np.asarray(audio.astype(mx.float32)).reshape(-1)
    assert np.isfinite(values).all()
    return (np.clip(values, -1, 1) * np.float32(32767)).astype('<i2').tobytes(), values


def check_budget(start):
    assert time.monotonic() - began < plan['total_budget_seconds'], 'Total diagnostic time budget'
    assert time.monotonic() - start < plan['individual_budget_seconds'], 'Individual diagnostic time budget'


original_sampler = qwen.Model._sample_token
observed = []
case_started = began


def observe(model, logits, *args, **kwargs):
    check_budget(case_started)
    value = original_sampler(model, logits, *args, **kwargs)
    # Keep the returned lazy array; do not force evaluation or take another draw.
    observed.append(value)
    return value


save()
try:
    mx.set_default_device(mx.gpu)
    mx.set_cache_limit(512 * 1024 * 1024)
    model = load((project / '.context/robustness/linked-models/tts').resolve(strict=True), lazy=False, strict=True)
    assert model.tokenizer is not None and model.speech_tokenizer is not None
    report['actual_model_source_sha256'] = sha(inspect.getfile(type(model)))
    report['actual_decoder_source_sha256'] = sha(inspect.getfile(type(model.speech_tokenizer)))
    report['actual_sampling_source_sha256'] = sha(inspect.getfile(sampling))
    assert report['actual_model_source_sha256'] == next(v for k, v in plan['source_sha256'].items() if k.endswith('/qwen3_tts.py'))
    assert report['actual_decoder_source_sha256'] == next(v for k, v in plan['source_sha256'].items() if k.endswith('/speech_tokenizer.py'))
    assert report['actual_sampling_source_sha256'] == next(v for k, v in plan['source_sha256'].items() if k.endswith('/sample_utils.py'))
    assert qwen.categorical_sampling.__module__ == 'mlx_audio.lm.sample_utils'
    eos = model.config.talker_config.codec_eos_token_id
    assert model.sample_rate == 24000
    capture_root = project / plan['native_capture']
    native = read(capture_root / 'report.json')
    assert native['status'] == 'diagnostic_completed' and len(native['results']) == 5
    for case in native['results']:
        case_started = time.monotonic()
        directory = base / ('decoder-' + case['id'])
        directory.mkdir()
        codec = []
        for row in case['steps']:
            path = capture_root / case['id'] / row['file']
            assert sha(path) == row['sha256']
            codec.append(np.asarray(mx.load(str(path))['codes']).reshape(-1).tolist())
        assert len(codec) == case['token_count'] and all(len(v) == 16 for v in codec)
        if case['eos_seen']:
            assert codec[-1][0] == eos
            codec = codec[:-1]
        assert all(v[0] != eos for v in codec)
        decoder = model.speech_tokenizer.decoder
        decoder.reset_streaming_state()
        chunks, floats = [], []
        for start in range(0, len(codec), 4):
            check_budget(case_started)
            codes = mx.array([codec[start:start + 4]], dtype=mx.int32).transpose(0, 2, 1)
            audio = decoder.streaming_step(codes).squeeze(1)[0]
            mx.eval(audio)
            pcm, values = pcm16(audio)
            chunks.append(pcm)
            floats.append(values)
        decoder.reset_streaming_state()
        data = b''.join(chunks)
        pcm_file = directory / 'reference.pcm'
        pcm_file.write_bytes(data)
        float_file = directory / 'reference.f32'
        float_file.write_bytes(np.concatenate(floats).astype('<f4').tobytes())
        original_pcm = capture_root / case['id'] / case['pcm_file']
        assert sha(original_pcm) == case['pcm_sha256']
        a = np.frombuffer(data, dtype='<i2').astype(np.int32)
        b = np.frombuffer(original_pcm.read_bytes(), dtype='<i2').astype(np.int32)
        assert a.shape == b.shape and data
        delta = a - b
        result = {'id': case['id'], 'codec_frames': len(codec), 'pcm_bytes': len(data),
                  'reference_pcm_sha256': sha(pcm_file), 'reference_f32_sha256': sha(float_file),
                  'native_pcm_sha256': case['pcm_sha256'], 'pcm_exact': data == original_pcm.read_bytes(),
                  'samples': len(a), 'different_samples': int(np.count_nonzero(delta)),
                  'maximum_pcm16_sample_delta': int(np.max(np.abs(delta))),
                  'mean_absolute_pcm16_sample_delta': float(np.mean(np.abs(delta))),
                  'elapsed_seconds': time.monotonic() - case_started}
        report['decoder_results'].append(result)
        mx.clear_cache()
        save()
        print('DECODER', case['id'], 'exact', result['pcm_exact'], 'max-delta', result['maximum_pcm16_sample_delta'], flush=True)
    for case in plan['cases']:
        case_started = time.monotonic()
        mx.random.seed(case['seed'])
        observed.clear()
        qwen.Model._sample_token = observe if case['observe_sampler'] else original_sampler
        chunks = []
        result_metadata = []
        for chunk in model.generate_custom_voice(
                text=case['text'], speaker=case['voice'], language=case['language'],
                temperature=plan['temperature'], max_tokens=plan['max_tokens'],
                top_k=plan['top_k'], top_p=plan['top_p'], repetition_penalty=plan['repetition_penalty'],
                stream=True, streaming_interval=plan['streaming_interval'], verbose=False):
            check_budget(case_started)
            assert chunk.sample_rate == 24000
            pcm, _ = pcm16(chunk.audio)
            chunks.append(pcm)
            result_metadata.append({'samples': int(chunk.samples), 'token_count': int(chunk.token_count)})
        qwen.Model._sample_token = original_sampler
        data = b''.join(chunks)
        assert data and len(data) <= 96 * 48000
        directory = base / case['id']
        directory.mkdir()
        pcm_file = directory / 'reference.pcm'
        pcm_file.write_bytes(data)
        result = {'id': case['id'], 'case': case, 'pcm_bytes': len(data), 'pcm_sha256': sha(pcm_file),
                  'audio_seconds': len(data) / 48000, 'chunks': result_metadata,
                  'processing_seconds': time.monotonic() - case_started}
        if case['observe_sampler']:
            # The reference samples all 16 groups, including the EOS step.
            assert observed and len(observed) % 16 == 0
            codes = np.array([int(v.item()) for v in observed], dtype=np.int32).reshape(-1, 16).tolist()
            main = [v[0] for v in codes]
            assert eos not in main[:-1]
            code_file = directory / 'codes.json'
            code_file.write_text(json.dumps(codes) + '\n')
            result.update(codes_sha256=sha(code_file), sampled_steps=len(codes), eos_seen=main[-1] == eos,
                          audio_codec_frames=len(codes) - (1 if main[-1] == eos else 0))
            assert len(data) == result['audio_codec_frames'] * 1920 * 2
        else:
            result.update(sampled_steps=None, eos_seen=None, audio_codec_frames=sum(x['token_count'] for x in result_metadata))
        report['results'].append(result)
        assert sum(r['pcm_bytes'] for r in report['results']) < plan['maximum_output_bytes']
        observed.clear()
        mx.clear_cache()
        save()
        print('REFERENCE', case['id'], 'EOS', result['eos_seen'], 'seconds', result['audio_seconds'], flush=True)
    first = report['results'][0]
    repeated = next(r for r in report['results'] if r['id'] == 'known-failure-repeat')
    direct = next(r for r in report['results'] if r['id'] == 'known-failure-unobserved')
    report['failure_repeat_pcm_exact'] = first['pcm_sha256'] == repeated['pcm_sha256']
    report['failure_repeat_codes_exact'] = first['codes_sha256'] == repeated['codes_sha256']
    report['observer_unobserved_pcm_exact'] = first['pcm_sha256'] == direct['pcm_sha256']
    assert report['failure_repeat_pcm_exact'] and report['failure_repeat_codes_exact'] and report['observer_unobserved_pcm_exact']
    report['status'] = 'diagnostic_completed'
except Exception as error:
    report.update(status='failed', error_type=type(error).__name__, error=str(error))
finally:
    qwen.Model._sample_token = original_sampler
    report['finished'] = datetime.now(timezone.utc).isoformat()
    save()
raise SystemExit(0 if report['status'] == 'diagnostic_completed' else 1)
