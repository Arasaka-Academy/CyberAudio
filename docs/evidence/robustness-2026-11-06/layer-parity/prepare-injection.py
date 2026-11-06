"""Create a one-variable in-memory reference control; no installed source edits."""
from pathlib import Path
import ast
import hashlib
import json

base = Path(__file__).resolve().parent
prior = base.parent / 'complete-codec-20261107'
root = base / 'native-frequency-injection'
root.mkdir(exist_ok=False)
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
snapshot = base / 'native-capture/known-failure/step-084.safetensors'
plan = json.loads((prior / 'plan.json').read_text())
plan.update(purpose='Causal one-variable reference control: only replace the in-memory main-talker inverse frequency with the exact captured native vector; force all16 original native codes, preserve installed source and every parameter',
            native_frequency_snapshot=str(snapshot), native_frequency_snapshot_sha256=sha(snapshot))
(root / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
source = (prior / 'compare-reference.py').read_text()
source = source.replace('project = base.parents[2]', 'project = base.parents[3]')
source = source.replace('native = json.loads((base / "native-capture/report.json").read_text())', 'native_root = base.parents[1] / "complete-codec-20261107/native-capture"\nnative = json.loads((native_root / "report.json").read_text())')
source = source.replace('sha(base / "native-capture/report.json")', 'sha(native_root / "report.json")')
source = source.replace('path = base / "native-capture" / case_id / item["file"]', 'path = native_root / case_id / item["file"]')
source = source.replace('assert native["plan_sha256"] == sha(base / "plan.json")', 'assert native["plan_sha256"] == sha(base.parents[1] / "complete-codec-20261107/plan.json")')
marker = '    assert model.tokenizer is not None and model.speech_tokenizer is not None'
assert source.count(marker) == 1
source = source.replace(marker, '''    frequency_snapshot = Path(plan["native_frequency_snapshot"])
    assert sha(frequency_snapshot) == plan["native_frequency_snapshot_sha256"]
    native_frequency = mx.load(str(frequency_snapshot))["inv_freq"]
    old_frequency = model.talker.model.rotary_emb._inv_freq
    report["only_in_memory_main_frequency_replaced"] = True
    report["original_frequency_float32_sha256"] = hashlib.sha256(materialize(old_frequency).tobytes()).hexdigest()
    report["replacement_frequency_float32_sha256"] = hashlib.sha256(materialize(native_frequency).tobytes()).hexdigest()
    model.talker.model.rotary_emb._inv_freq = native_frequency
''' + marker)
ast.parse(source)
(root / 'compare-reference.py').write_text(source)
runner = (prior / 'run-reference.py').read_text()
runner = runner.replace('project = base.parents[2]', 'project = base.parents[3]')
runner = runner.replace('base / "native-build/build.json"', 'base.parent / "native-build/build.json"')
runner = runner.replace('base / "native-runner.json"', 'base.parent / "reference-runner.json"')
runner = runner.replace('base.parent / "reference-parity-20261107/', 'base.parents[1] / "reference-parity-20261107/')
ast.parse(runner)
(root / 'run-reference.py').write_text(runner)
print('Prepared single-variable injection against all six full captured trajectories')
