from pathlib import Path
import ast
import hashlib
import json

base = Path(__file__).resolve().parent
prior = base.parent / 'complete-codec-20261107'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
plan = json.loads((prior / 'plan.json').read_text())
plan.update(
    purpose='Localize first main-talker forward mismatch at zero-based step84 using captured inputs and layer boundaries; all replayed logits and hidden must match original native snapshots before interpretation',
    cases=[c for c in plan['cases'] if c['id'] in ['known-failure', 'eric-english-numeric', 'known-failure-repeat']],
    capture_report=str(prior / 'native-capture/report.json'),
    capture_report_sha256=sha(prior / 'native-capture/report.json'),
    maximum_snapshot_bytes=268435456,
    trace_steps={'known-failure': [82, 83, 84], 'eric-english-numeric': [79, 80], 'known-failure-repeat': [82, 83, 84]},
)
assert not (base / 'plan.json').exists()
(base / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
previous = (prior / 'build-native.py').read_text()
prefix = previous[:previous.index('changed = source.read_text()')]
prefix = prefix.replace('protected_paths = [source,', 'talker = source.with_name("Qwen3TTSTalker.swift")\nprotected_paths = [source, talker,')
transform = (base / 'instrument-private.py').read_text()
tail = previous[previous.index('old_map = json.loads'):]
tail = tail.replace('str(private) if name == str(source) else name', 'str(private) if name == str(source) else str(private_talker) if name == str(talker) else name')
tail = tail.replace('str(private) if p == str(source) else p', 'str(private) if p == str(source) else str(private_talker) if p == str(talker) else p')
tail = tail.replace('CompleteCodecProbe', 'LayerParityProbe').replace('complete-codec-probe', 'layer-parity-probe')
tail = tail.replace('Full native order-candidate codec/forward observation; exact original audio required', 'Main talker layer observation; exact original captured forward outputs required')
tail = tail.replace('"diagnostic_sdk_sha256": sha(private),', '"diagnostic_sdk_sha256": sha(private), "original_talker_sha256": sha(talker), "diagnostic_talker_sha256": sha(private_talker),')
script = prefix + transform + '\n' + tail
ast.parse(script)
(base / 'build-native.py').write_text(script)
print('Prepared isolated per-layer build')
