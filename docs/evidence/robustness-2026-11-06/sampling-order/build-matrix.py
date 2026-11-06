"""Link the same managed-entry matrix driver to baseline and private order modules."""
from pathlib import Path
import hashlib
import json
import subprocess
import time

base = Path(__file__).resolve().parent
project = base.parents[2]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
prior_root = base / 'order-build'
prior = json.loads((prior_root / 'build.json').read_text())
assert prior['status'] == 'built'
protected = prior['preserved_input_sha256']
assert protected == {p: sha(p) for p in protected}
root = base / 'matrix-build'
root.mkdir(exist_ok=False)
description = json.loads((project / '.context/icon-native-build/arm64-apple-macosx/release/description.json').read_text())
tts = description['swiftCommands']['C.MLXAudioTTS-arm64-apple-macosx-release.module']
compile_command = list(prior['compile_command'])
compile_command[compile_command.index('-o')+1] = str(root / 'OrderMatrixProbe.o')
compile_command[-1] = str(base / 'OrderMatrixProbe.swift')
lines = (prior_root / 'Objects.LinkFileList').read_text().splitlines()
old_main = str(prior_root / 'OrderCandidateProbe.o')
assert lines.count(old_main) == 1
mapping = {str(prior_root / 'MLXAudioTTS.build' / Path(p).name): p for p in tts['objects']}
receipt = {'status': 'building', 'plan_sha256': sha(base / 'matrix-plan.json'),
           'source_sha256': sha(base / 'OrderMatrixProbe.swift'), 'preserved_input_sha256': protected}
started = time.monotonic()
try:
    with (root / 'compile.log').open('xb') as log:
        subprocess.run(compile_command, check=True, timeout=180, stdout=log, stderr=subprocess.STDOUT)
    receipt['executables'] = {}
    for stage in ['baseline', 'candidate']:
        filelist = root / (stage + '.Objects.LinkFileList')
        filelist.write_text('\n'.join(str(root / 'OrderMatrixProbe.o') if p == old_main else
            mapping.get(p,p) if stage == 'baseline' else p for p in lines)+'\n')
        link = list(prior['link_command'])
        link[link.index('-o')+1] = str(root / (stage + '-matrix-probe'))
        link[link.index('@' + str(prior_root / 'Objects.LinkFileList'))] = '@' + str(filelist)
        with (root / (stage + '.log')).open('xb') as log:
            subprocess.run(link, check=True, timeout=180, stdout=log, stderr=subprocess.STDOUT)
        receipt['executables'][stage] = {'sha256': sha(root / (stage + '-matrix-probe')), 'link_command': link}
    receipt.update(status='built', compile_command=compile_command)
except Exception as error:
    receipt.update(status='failed', error=str(error))
    raise
finally:
    receipt['elapsed_seconds'] = time.monotonic()-started
    receipt['shared_and_production_inputs_preserved'] = protected == {p: sha(p) for p in protected}
    (root / 'build.json').write_text(json.dumps(receipt, indent=2)+'\n')
assert receipt['shared_and_production_inputs_preserved']
print('Both baseline/candidate matrix binaries built with the same driver')
