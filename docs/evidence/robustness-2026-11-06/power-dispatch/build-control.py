"""One private C++ dispatch change, model-free scalar baseline/control."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import shlex
import signal
import subprocess
import time
import urllib.request
import yaml

base = Path(__file__).resolve().parent
project = base.parents[2]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
prior = base.parent / 'layer-parity-20261107'
build = json.loads((prior / 'native-build/build.json').read_text())
commands = yaml.safe_load((project / '.context/icon-native-build/release.yaml').read_text())['commands']
name, command = next((n, c) for n, c in commands.items() if n.endswith('/metal/jit_kernels.cpp.o'))
source = Path(command['inputs'][0])
original_object = Path(name)
protected = {**build['preserved_input_sha256'], str(source): sha(source), str(original_object): sha(original_object)}
for p, digest in protected.items():
    assert sha(p) == digest
root = base / 'native-build'
root.mkdir(exist_ok=False)
modified = source.read_text()
before = '''    const char* op) {
  std::string lib_name = kernel_name.substr(kernel_name.find("_") + 1);
  auto lib = d.get_library(lib_name, [&]() {'''
start = modified.index('MTL::ComputePipelineState* get_binary_kernel(')
end = modified.index('MTL::ComputePipelineState* get_binary_two_kernel(', start)
segment = modified[start:end]
assert segment.count(before) == 1
segment = segment.replace(before, '''    const char* op) {
  // PRIVATE DIAGNOSTIC: only float32 Power dispatch uses the shipped library.
  if (std::string(op) == "Power" && in_type == float32 && out_type == float32) {
    return d.get_kernel(kernel_name);
  }
  std::string lib_name = kernel_name.substr(kernel_name.find("_") + 1);
  auto lib = d.get_library(lib_name, [&]() {''')
modified = modified[:start] + segment + modified[end:]
private = root / 'jit_kernels.cpp'
private.write_text(modified)
args = list(command['args'])
args[args.index(str(source))] = str(private)
args[args.index('-o') + 1] = str(root / 'jit_kernels.cpp.o')
args[args.index('-MF') + 1] = str(root / 'jit_kernels.cpp.d')
old_list = prior / 'rope-scalar-build/Objects.LinkFileList'
control_list = root / 'control.LinkFileList'
patched_list = root / 'precompiled-power.LinkFileList'
lines = old_list.read_text()
assert lines.count(str(original_object)) == 1
control_list.write_text(lines)
patched_list.write_text(lines.replace(str(original_object), str(root / 'jit_kernels.cpp.o')))
app = project / '.context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app'
shader = app / 'Contents/Resources/mlx.metallib'
plan = {'purpose': 'Causal model-free control: preserve the complete native runtime except float32 Power dispatch; compare original JIT versus same shipped precompiled kernel on the identical Swift scalar formula',
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'source_sha256': sha(source), 'original_object_sha256': sha(original_object),
        'private_source_sha256': sha(private), 'swift_probe_source_sha256': sha(prior / 'RopeScalarProbe.swift'),
        'shader_sha256': sha(shader), 'preserved_input_sha256': protected,
        'scope': ['No model loads', 'No math-mode or language-version changes', 'Only float32 Power bypasses JIT',
                  'Original native vector required for baseline and prior reference vector required for precompiled control',
                  'All other dispatch, source, compiled objects, installed model/defaults remain untouched'],
        'each_compile_or_link_seconds': 180, 'each_scalar_seconds': 60}
plan_path = base / 'plan.json'
assert not plan_path.exists()
plan_path.write_text(json.dumps(plan, indent=2) + '\n')
production = Path.home() / 'Library/Application Support/CyberAudio'


def snapshot():
    with urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=5) as response:
        state = json.load(response)
    return {'instance': state['instance'], 'status': state['status'], 'busy': state['busy'],
            'config_sha256': sha(production / 'config/service.json'), 'token_sha256': sha(production / 'config/api-token')}


receipt = {'status': 'running', 'started': datetime.now(timezone.utc).isoformat(),
           'production_before': snapshot(), 'plan_sha256': sha(plan_path), 'model_loaded': False,
           'source_sha256': sha(base / 'build-control.py'), 'steps': []}
assert receipt['production_before']['status'] == 'ready' and not receipt['production_before']['busy']
start_time = time.monotonic()
process = None


def save():
    receipt['elapsed_seconds'] = time.monotonic() - start_time
    (base / 'runner.json').write_text(json.dumps(receipt, indent=2) + '\n')


def run(name, command, timeout):
    global process
    step = {'name': name, 'args': command, 'status': 'running'}
    receipt['steps'].append(step)
    save()
    with (root / (name + '.txt')).open('xb') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        step['owned_group_pid'] = process.pid
        save()
        process.wait(timeout=timeout)
    step['exit_code'] = process.returncode
    step['status'] = 'completed' if process.returncode == 0 else 'failed'
    save()
    assert process.returncode == 0, name + ' failed; retained log'
    process = None


save()
try:
    run('compile', args, plan['each_compile_or_link_seconds'])
    for label, filelist, expected in [
        ('original-jit', control_list, prior / 'native-capture/known-failure/step-084.safetensors'),
        ('precompiled-power', patched_list, prior / 'reference-capture/known-failure/step-084.safetensors')]:
        link = [a.replace('LayerParityProbe', 'RopeScalarProbe') for a in build['link_command']]
        executable = root / label
        link[link.index('-o') + 1] = str(executable)
        link[link.index('@' + str(prior / 'native-build/Objects.LinkFileList'))] = '@' + str(filelist)
        assert all(Path(shlex.split(line)[0]).is_file() for line in filelist.read_text().splitlines())
        run(label + '-link', link, plan['each_compile_or_link_seconds'])
        run(label + '-scalar', ['/usr/bin/sandbox-exec', '-f', str(app / 'Contents/Resources/offline.sb'),
            str(executable), str(shader), str(base / (label + '.safetensors')), str(expected)], plan['each_scalar_seconds'])
    receipt['status'] = 'diagnostic_completed'
except Exception as error:
    receipt.update(status='failed', error_type=type(error).__name__, error=str(error))
finally:
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)
    receipt['production_after'] = snapshot()
    receipt['production_preserved'] = receipt['production_before'] == receipt['production_after']
    receipt['shared_inputs_preserved'] = protected == {p: sha(p) for p in protected}
    if not receipt['production_preserved'] or not receipt['shared_inputs_preserved']:
        receipt['status'] = 'failed'
    receipt['finished'] = datetime.now(timezone.utc).isoformat()
    save()
print(receipt['status'], round(receipt['elapsed_seconds'], 2), 'seconds')
raise SystemExit(0 if receipt['status'] == 'diagnostic_completed' else 1)
