"""Teacher-force native codec vectors through the unmodified local reference."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import importlib.metadata
import inspect
import json
import sys
import time

import mlx.core as mx
import numpy as np
from mlx_audio.tts.utils import load

base = Path(__file__).resolve().parent
project = base.parents[2]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
plan = json.loads((base / "plan.json").read_text())
native = json.loads((base / "native-capture/report.json").read_text())
output = base / "reference-comparison.json"
assert not output.exists()
assert native["status"] == "diagnostic_completed"
assert native["plan_sha256"] == sha(base / "plan.json")
assert all(r["original_candidate_tokens_exact"] and r["original_candidate_pcm_exact"] for r in native["results"])
assert importlib.metadata.version("mlx") == plan["python_mlx"]
assert importlib.metadata.version("mlx-audio") == plan["python_mlx_audio"]
started = time.monotonic()
report = {"status": "running", "started": datetime.now(timezone.utc).isoformat(),
          "purpose": plan["purpose"], "plan_sha256": sha(base / "plan.json"),
          "native_report_sha256": sha(base / "native-capture/report.json"),
          "versions": {name: importlib.metadata.version(name) for name in
                       ["mlx", "mlx-audio", "mlx-lm", "numpy", "transformers", "tokenizers"]},
          "sampling": "None; all sixteen native codec IDs are teacher-forced", "results": []}


def save():
    report["elapsed_seconds"] = time.monotonic() - started
    temp = output.with_suffix(".json.tmp")
    temp.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    temp.replace(output)


def materialize(array):
    return np.asarray(array.astype(mx.float32))


def compare(actual, expected):
    result = {"reference_shape": list(actual.shape), "native_shape": list(expected.shape),
              "reference_dtype": str(actual.dtype), "native_dtype": str(expected.dtype),
              "shape_equal": actual.shape == expected.shape,
              "dtype_equal": actual.dtype == expected.dtype}
    a, b = materialize(actual), materialize(expected)
    assert np.isfinite(a).all() and np.isfinite(b).all(), "Nonfinite forward output"
    if result["shape_equal"]:
        delta = a.astype(np.float64) - b.astype(np.float64)
        result.update(values_exact=bool(np.array_equal(a, b)),
                      max_abs=float(np.max(np.abs(delta))), mean_abs=float(np.mean(np.abs(delta))),
                      rms_abs=float(np.sqrt(np.mean(delta * delta))))
    return result


def snapshot(case_id, item):
    path = base / "native-capture" / case_id / item["file"]
    assert path.stat().st_size == item["bytes"] and sha(path) == item["sha256"]
    arrays = mx.load(str(path))
    assert set(arrays) == set(item["arrays"])
    for name, array in arrays.items():
        assert list(array.shape) == item["arrays"][name]["shape"]
    return arrays


def eos_metrics(logits, eos, previous, vocab):
    values = materialize(logits).reshape(-1).astype(np.float64)
    raw = float(values[eos])
    raw_rank = int(np.count_nonzero(values > raw)) + 1
    values = values.copy()
    for token in set(previous):
        values[token] = values[token] * plan["repetition_penalty"] if values[token] < 0 else values[token] / plan["repetition_penalty"]
    values[vocab - 1024:] = -np.inf
    values[eos] = raw
    rank = int(np.count_nonzero(values > values[eos])) + 1
    return {"raw_logit": raw, "raw_rank": raw_rank, "eligible_rank": rank,
            "within_top_k": rank <= plan["top_k"], "top_token": int(np.argmax(values)),
            "top_minus_eos": float(np.max(values) - values[eos])}


save()
try:
    mx.set_default_device(mx.gpu)
    mx.set_cache_limit(512 * 1024 * 1024)
    model_path = (project / ".context/robustness/linked-models/tts").resolve(strict=True)
    model = load(model_path, lazy=False, strict=True)
    source = Path(inspect.getfile(type(model)))
    report["reference_source_sha256"] = sha(source)
    assert sha(source) == plan["python_model_source_sha256"], "Reference source changed"
    assert model.tokenizer is not None and model.speech_tokenizer is not None
    report["tokenizer_class"] = type(model.tokenizer).__name__
    report["tokenizer_loaded"] = True
    report["speech_tokenizer_loaded"] = True
    for case, captured in zip(plan["cases"], native["results"], strict=True):
        assert case == captured["case"]
        case_started = time.monotonic()
        prepared = snapshot(case["id"], captured["prepared"])
        input_embeds, trailing, pad = model._prepare_generation_inputs(
            case["text"], language=case["language"], speaker=case["voice"])
        chat = f"<|im_start|>assistant\n{case['text']}<|im_end|>\n<|im_start|>assistant\n"
        ids = mx.array(model.tokenizer.encode(chat))[None, :]
        result = {"id": case["id"], "case": case, "prepared": {
            name: compare(array, prepared[name]) for name, array in {
                "input_embeds": input_embeds, "trailing_text_hidden": trailing,
                "tts_pad_embed": pad, "input_ids": ids}.items()}, "steps": []}
        report["results"].append(result)
        cache = model.talker.make_cache()
        code_cache = model.talker.code_predictor.make_cache()
        previous = []
        eos = model.config.talker_config.codec_eos_token_id
        for index, item in enumerate(captured["steps"]):
            assert index < plan["maximum_steps_per_case"]
            assert time.monotonic() - case_started < plan["individual_budget_seconds"]
            assert time.monotonic() - started < plan["reference_total_budget_seconds"]
            arrays = snapshot(case["id"], item)
            codes = arrays["codes"].astype(mx.int32)
            assert codes.shape == (1, 16) and int(codes[0, 0].item()) == captured["tokens"][index]
            before = compare(input_embeds, arrays["input_embeds"])
            logits, hidden = model.talker(input_embeds, cache=cache)
            native_logits = arrays["logits"]
            main_logits = logits[:, -1:, :]
            code_tokens = [codes[:, :1]]
            model._reset_code_cache(code_cache)
            code_logits_list = []
            for code_index in range(15):
                if code_index == 0:
                    code_input = mx.concatenate([hidden[:, -1:, :], model.talker.get_input_embeddings()(code_tokens[0])], axis=1)
                else:
                    code_input = model.talker.code_predictor.codec_embedding[code_index - 1](code_tokens[-1])
                code_logits, code_cache, _ = model.talker.code_predictor(
                    code_input, cache=code_cache, generation_step=code_index)
                code_logits_list.append(code_logits[:, -1:, :])
                code_tokens.append(codes[:, code_index + 1:code_index + 2])
            text_embed = trailing[:, index:index + 1, :] if index < trailing.shape[1] else pad
            input_embeds = text_embed + model._codec_embeds_for_tokens(code_tokens)
            mx.eval(input_embeds, main_logits, hidden, *code_logits_list)
            code_ref = mx.concatenate(code_logits_list, axis=1)
            step = {"step": index, "forced_codes": np.asarray(codes).tolist()[0],
                    "comparisons": {"input_embeds": before,
                        "logits": compare(main_logits, native_logits),
                        "hidden": compare(hidden[:, -1:, :], arrays["hidden"]),
                        "code_logits": compare(code_ref, arrays["code_logits"]),
                        "next_input_embeds": compare(input_embeds, arrays["next_input_embeds"])},
                    "reference_eos": eos_metrics(main_logits, eos, previous, model.config.talker_config.vocab_size),
                    "native_eos": eos_metrics(native_logits, eos, previous, model.config.talker_config.vocab_size),
                    "auxiliary_top1_equal": int(np.count_nonzero(
                        np.argmax(materialize(code_ref), axis=-1) ==
                        np.argmax(materialize(arrays["code_logits"]), axis=-1)))}
            result["steps"].append(step)
            previous.append(int(codes[0, 0].item()))
        result["processing_seconds"] = time.monotonic() - case_started
        result["completed_steps"] = len(result["steps"])
        save()
        print("COMPARED", case["id"], len(result["steps"]), "steps", flush=True)
        del cache, code_cache
        mx.clear_cache()
    report["status"] = "diagnostic_completed"
except Exception as error:
    report["status"] = "failed"
    report["error_type"] = type(error).__name__
    report["error"] = str(error)
    raise
finally:
    report["finished"] = datetime.now(timezone.utc).isoformat()
    save()
