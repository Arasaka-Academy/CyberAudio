"""Extract the actual native sampler and add only its final-logit observation."""
from pathlib import Path
import hashlib
import json
import os
import shlex
import signal
import subprocess
import time

base = Path(__file__).resolve().parent
project = base.parents[2]
prior_root = base.parent / "reference-parity-20261107/native-build"
prior = json.loads((prior_root / "build.json").read_text())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
protected = prior["preserved_input_sha256"]
assert protected == {p: sha(p) for p in protected}
source = next(Path(p) for p in protected if p.endswith('/Qwen3TTS.swift'))
text = source.read_text()
begin = text.index('    func sampleToken(')
end = text.index('    // MARK: - fromPretrained', begin)
sampler = text[begin:end]
assert sampler.count('let token = categorical(filteredLogits / temperature)') == 1
observed = sampler.replace('    func sampleToken(', '    static func sampleToken(', 1)
observed = observed.replace('        minP: Float = 0.0\n',
    '        minP: Float = 0.0,\n        onFiltered: (MLXArray) -> Void\n', 1)
observed = observed.replace('        let token = categorical(filteredLogits / temperature)',
    '        onFiltered(filteredLogits / temperature)\n        let token = categorical(filteredLogits / temperature)', 1)
root = base / 'native-build'
root.mkdir(exist_ok=False)
template = (base / 'SamplingParityProbe.swift.in').read_text()
assert template.count('// EXTRACTED_SAMPLER') == 1
main = root / 'SamplingParityProbe.swift'
main.write_text(template.replace('// EXTRACTED_SAMPLER', observed))
compile_command = list(prior['compile_command'])
compile_command[compile_command.index('-o') + 1] = str(root / 'SamplingParityProbe.o')
compile_command[-1] = str(main)
filelist = root / 'Objects.LinkFileList'
old_main = str(prior_root / 'ReferenceParityProbe.o')
lines = (prior_root / 'Objects.LinkFileList').read_text().splitlines()
assert lines.count(old_main) == 1
filelist.write_text('\n'.join(str(root / 'SamplingParityProbe.o') if p == old_main else p for p in lines) + '\n')
link_command = list(prior['link_command'])
link_command[link_command.index('-o') + 1] = str(root / 'sampling-parity-probe')
link_command[link_command.index('@' + str(prior_root / 'Objects.LinkFileList'))] = '@' + str(filelist)
receipt = {'status': 'building', 'original_sdk_sha256': sha(source),
           'sampler_body_sha256': hashlib.sha256(sampler.encode()).hexdigest(),
           'instrumented_sampler_sha256': hashlib.sha256(observed.encode()).hexdigest(),
           'source_sha256': sha(main), 'template_sha256': sha(base / 'SamplingParityProbe.swift.in'),
           'plan_sha256': sha(base / 'plan.json'), 'preserved_input_sha256': protected}
started = time.monotonic()
try:
    for name, command in [('compile', compile_command), ('link', link_command)]:
        child = None
        try:
            with (root / (name + '.log')).open('xb') as log:
                child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                assert child.wait(timeout=180) == 0, f'{name} failed'
        finally:
            if child is not None and child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try: child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=15)
    receipt.update(status='built', executable_sha256=sha(root / 'sampling-parity-probe'),
                   compile_command=compile_command, link_command=link_command)
except Exception as error:
    receipt.update(status='failed', error=str(error))
    raise
finally:
    receipt['elapsed_seconds'] = time.monotonic() - started
    receipt['shared_and_production_inputs_preserved'] = protected == {p: sha(p) for p in protected}
    (root / 'build.json').write_text(json.dumps(receipt, indent=2) + '\n')
assert receipt['shared_and_production_inputs_preserved']
print('Source-extracted native sampler probe built')
