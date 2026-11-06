"""Compile a private decoder caller against unchanged native model objects."""
from pathlib import Path
import hashlib
import json
import os
import signal
import subprocess
import time

base = Path(__file__).resolve().parent
project = base.parents[2]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
root = base / 'decoder-build'
root.mkdir()
prior_root = project / '.context/robustness/m4-acceptance-20261106/seeded-tts-build'
prior = read(prior_root / 'build.json')
protected = read(base.parent / 'sampling-parity-20261107/native-build/build.json')['preserved_input_sha256']
assert protected == {p: sha(p) for p in protected}
compile_command = [arg.replace('SeededTTSProbe', 'ReferenceDecodeProbe') for arg in prior['compile_command']]
compile_command[compile_command.index('-o') + 1] = str(root / 'ReferenceDecodeProbe.o')
compile_command[compile_command.index('-module-cache-path') + 1] = str(root / 'ModuleCache')
compile_command[-1] = str(base / 'ReferenceDecodeProbe.swift')
compile_command[1:1] = ['-Xfrontend', '-disable-access-control']
old_list = prior_root / 'Objects.LinkFileList'
lines = old_list.read_text().splitlines()
old_main = str(prior_root / 'SeededTTSProbe.o')
assert lines.count(old_main) == 1
filelist = root / 'Objects.LinkFileList'
filelist.write_text('\n'.join(str(root / 'ReferenceDecodeProbe.o') if p == old_main else p for p in lines) + '\n')
link_command = [arg.replace('SeededTTSProbe', 'ReferenceDecodeProbe') for arg in prior['link_command']]
link_command[link_command.index('-o') + 1] = str(root / 'reference-decode-probe')
link_command[link_command.index('@' + str(old_list))] = '@' + str(filelist)
receipt = {'status': 'building', 'source_sha256': sha(base / 'ReferenceDecodeProbe.swift'),
           'plan_sha256': sha(base / 'decoder-followup-plan.json'), 'preserved_input_sha256': protected,
           'compile_command': compile_command, 'link_command': link_command}
began = time.monotonic()
try:
    for name, args in [('compile', compile_command), ('link', link_command)]:
        process = None
        try:
            with (root / (name + '.log')).open('xb') as log:
                process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                assert process.wait(timeout=180) == 0, name + ' failed; retained log'
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=12)
    receipt.update(status='built', executable_sha256=sha(root / 'reference-decode-probe'))
except Exception as error:
    receipt.update(status='failed', error_type=type(error).__name__, error=str(error))
finally:
    receipt['elapsed_seconds'] = time.monotonic() - began
    receipt['shared_and_production_inputs_preserved'] = protected == {p: sha(p) for p in protected}
    (root / 'build.json').write_text(json.dumps(receipt, indent=2) + '\n')
assert receipt['status'] == 'built' and receipt['shared_and_production_inputs_preserved']
print('Private native decoder caller built; no SDK source/object replacements')
