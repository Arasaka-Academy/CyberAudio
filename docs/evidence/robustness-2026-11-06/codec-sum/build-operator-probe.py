import hashlib
import json
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
BUILD = ROOT / 'native-operator-build'
BUILD.mkdir(exist_ok=False)
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
prior_root = ROOT.parent / 'm4-acceptance-20261106/seeded-tts-build'
prior = json.loads((prior_root / 'build.json').read_text())
compile_command = [x.replace('SeededTTSProbe', 'NativeOperatorProbe') for x in prior['compile_command']]
compile_command[compile_command.index('-o') + 1] = str(BUILD / 'NativeOperatorProbe.o')
compile_command[-1] = str(ROOT / 'NativeOperatorProbe.swift')
compile_command[compile_command.index('-module-cache-path') + 1] = str(BUILD / 'ModuleCache')
old_list = prior_root / 'Objects.LinkFileList'
lines = old_list.read_text().splitlines()
old_main = str(prior_root / 'SeededTTSProbe.o')
assert lines.count(old_main) == 1
protected = {shlex.split(x)[0]: sha(shlex.split(x)[0]) for x in lines}
filelist = BUILD / 'Objects.LinkFileList'
filelist.write_text('\n'.join(str(BUILD / 'NativeOperatorProbe.o') if x == old_main else x for x in lines) + '\n')
link = [x.replace('SeededTTSProbe', 'NativeOperatorProbe') for x in prior['link_command']]
link[link.index('-o') + 1] = str(BUILD / 'native-operator-probe')
link[link.index('@' + str(old_list))] = '@' + str(filelist)
record = {'status': 'building', 'source_sha256': sha(ROOT / 'NativeOperatorProbe.swift'), 'protected_inputs': protected, 'steps': []}
try:
    for name, command in [('compile', compile_command), ('link', link)]:
        with (BUILD / (name + '.private.log')).open('xb') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=60)
        record['steps'].append({'step': name, 'exit_code': result.returncode})
        assert result.returncode == 0, name
    record['status'] = 'built'
    record['executable_sha256'] = sha(BUILD / 'native-operator-probe')
finally:
    record['shared_inputs_preserved'] = protected == {name: sha(name) for name in protected}
    (BUILD / 'build.json').write_text(json.dumps(record, indent=2) + '\n')
assert record['shared_inputs_preserved']
print(json.dumps({k:v for k,v in record.items() if k != 'protected_inputs'}))
