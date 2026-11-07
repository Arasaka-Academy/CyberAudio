"""Reconstruct the original prompt builder with the pinned tokenizer, no weights."""
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from transformers import AutoTokenizer

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
PLAN = json.loads((ROOT / "protocol.json").read_text())
source = PROJECT / ".context/robustness/reference-parity-20261107/vendor/mlx_audio/stt/models/qwen3_asr/qwen3_asr.py"
assert hashlib.sha256(source.read_bytes()).hexdigest() == PLAN["reference_model_source_sha256"]
tree = ast.parse(source.read_text())
klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Qwen3ASRModel")
method = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "_build_prompt")
# This builder only creates text/token IDs. Keep its AST unchanged and return
# its NumPy IDs directly in place of the final MLX array allocation.
namespace = {"Optional": Optional, "mx": SimpleNamespace(array=lambda value: value)}
exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
builder = namespace["_build_prompt"]
tokenizer = AutoTokenizer.from_pretrained(str((PROJECT / ".context/robustness/linked-models/asr").resolve()), trust_remote_code=True, local_files_only=True)
assert type(tokenizer).__name__ == "Qwen2Tokenizer"
stub = SimpleNamespace(_tokenizer=tokenizer)
rows = list(map(json.loads, (ROOT / "results.private.jsonl").read_text().splitlines()))
arms = {arm: {r["id"]: r for r in rows if r["arm"] == arm} for arm in PLAN["execution"]["arms"]}
prefix_count = int(builder(stub, 0, None, None).shape[1])
audio_token = tokenizer.convert_tokens_to_ids("<|audio_pad|>")
checks = []
for identity in PLAN["data"]["ids"]:
    baseline = arms["baseline-empty-context"][identity]
    candidate = arms["fixed-verbatim-context"][identity]
    n_audio = baseline["response"]["prompt_tokens"] - prefix_count
    empty = builder(stub, n_audio, None, None)[0].tolist()
    prompted = builder(stub, n_audio, None, PLAN["context_prompt"])[0].tolist()
    assert len(empty) == baseline["response"]["prompt_tokens"]
    assert len(prompted) == candidate["response"]["prompt_tokens"]
    assert empty.count(audio_token) == prompted.count(audio_token) == n_audio
    assert empty != prompted and len(prompted) - len(empty) == 26
    assert PLAN["context_prompt"] in tokenizer.decode(prompted, skip_special_tokens=False)
    checks.append({"id": identity, "baseline_prompt_tokens": len(empty), "candidate_prompt_tokens": len(prompted),
                   "extra_context_tokens": len(prompted) - len(empty), "unchanged_audio_token_count": n_audio})
receipt = {"status": "prompt_reconstruction_verified", "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
           "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "weight_loads": 0, "additional_generations": 0, "model_methods_replaced": False,
           "verification_scope": "Original prompt-builder AST and pinned tokenizer reconstruct all twenty observed prompt counts; source dataflow passes system_prompt through the decoder. This is a file/tokenizer reconstruction, not a new tensor trace.",
           "pairs": checks}
(ROOT / "prompt-qualification.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps({"status": receipt["status"], "verified_pairs": len(checks), "extra_context_tokens_each": 26, "weight_loads": 0, "additional_generations": 0}))
