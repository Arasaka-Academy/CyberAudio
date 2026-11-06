"""Compile a model-free MLX scalar control using only private outputs."""
from pathlib import Path
import hashlib
import json
import shlex
import subprocess
import time

base = Path(__file__).resolve().parent
project = base.parents[2]
build = json.loads((base / 'native-build/build.json').read_text())
root = base / 'rope-scalar-build'
root.mkdir(exist_ok=False)
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
args = list(build['compile_command'])
args[args.index('-o') + 1] = str(root / 'RopeScalarProbe.o')
args[-1] = str(base / 'RopeScalarProbe.swift')
args = [a.replace('LayerParityProbe', 'RopeScalarProbe') for a in args]
filelist = root / 'Objects.LinkFileList'
old = base / 'native-build/Objects.LinkFileList'
filelist.write_text(old.read_text().replace(str(base / 'native-build/LayerParityProbe.o'), str(root / 'RopeScalarProbe.o')))
link = list(build['link_command'])
link[link.index('-o') + 1] = str(root / 'rope-scalar-probe')
link[link.index('@' + str(old))] = '@' + str(filelist)
app = project / '.context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app'
run = ['/usr/bin/sandbox-exec', '-f', str(app / 'Contents/Resources/offline.sb'), str(root / 'rope-scalar-probe'),
       str(app / 'Contents/Resources/mlx.metallib'), str(base / 'native-rope-formula.safetensors'),
       str(base / 'native-capture/known-failure/step-084.safetensors')]
started = time.monotonic()
report = {'status': 'running', 'source_sha256': sha(base / 'RopeScalarProbe.swift'), 'model_loaded': False}
try:
    for name, command in [('compile', args), ('link', link), ('run', run)]:
        with (root / (name + '.txt')).open('xb') as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=60)
            report[name + '_exit_code'] = completed.returncode
            assert completed.returncode == 0, name
    report['output_sha256'] = sha(base / 'native-rope-formula.safetensors')
    report['status'] = 'diagnostic_completed'
finally:
    report['elapsed_seconds'] = time.monotonic() - started
    report['shared_inputs_preserved'] = build['preserved_input_sha256'] == {p: sha(p) for p in build['preserved_input_sha256']}
    (base / 'native-rope-runner.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
