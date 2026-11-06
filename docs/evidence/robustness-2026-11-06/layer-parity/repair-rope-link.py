"""Retain the failed symbol-alias link and reuse its successful object compilation."""
from pathlib import Path
import hashlib
import json
import subprocess
import time

base = Path(__file__).resolve().parent
project = base.parents[2]
build = json.loads((base / 'native-build/build.json').read_text())
prior = json.loads((base / 'native-rope-runner.json').read_text())
assert prior['compile_exit_code'] == 0 and prior['link_exit_code'] == 1
root = base / 'rope-scalar-build'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
link = [arg.replace('LayerParityProbe', 'RopeScalarProbe') for arg in build['link_command']]
link[link.index('-o') + 1] = str(root / 'rope-scalar-probe')
old = base / 'native-build/Objects.LinkFileList'
link[link.index('@' + str(old))] = '@' + str(root / 'Objects.LinkFileList')
app = project / '.context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app'
run = ['/usr/bin/sandbox-exec', '-f', str(app / 'Contents/Resources/offline.sb'), str(root / 'rope-scalar-probe'),
       str(app / 'Contents/Resources/mlx.metallib'), str(base / 'native-rope-formula.safetensors'),
       str(base / 'native-capture/known-failure/step-084.safetensors')]
path = base / 'native-rope-repair.json'
assert not path.exists()
report = {'status': 'running', 'source_sha256': sha(base / 'repair-rope-link.py'),
          'retained_first_attempt_sha256': sha(base / 'native-rope-runner.json'), 'model_loaded': False,
          'reused_compilation': True}
start = time.monotonic()
try:
    for name, command in [('link', link), ('run', run)]:
        with (root / (name + '-followup.txt')).open('xb') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=60)
        report[name + '_exit_code'] = result.returncode
        assert result.returncode == 0, name
    report['output_sha256'] = sha(base / 'native-rope-formula.safetensors')
    report['status'] = 'diagnostic_completed'
finally:
    report['elapsed_seconds'] = time.monotonic() - start
    report['shared_inputs_preserved'] = build['preserved_input_sha256'] == {p: sha(p) for p in build['preserved_input_sha256']}
    path.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
