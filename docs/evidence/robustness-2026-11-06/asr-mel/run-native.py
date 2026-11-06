"""Bounded, serial model-free compile/link/capture with owned process cleanup."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import signal
import subprocess
import time
import urllib.request

base = Path(__file__).resolve().parent
project = base.parents[2]
read = lambda p: json.loads(Path(p).read_text())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
protected = read(base.parent / 'power-kernel-20261107/generation-build/build.json')['preserved_input_sha256']
assert protected == {p: sha(p) for p in protected}
production = Path.home() / 'Library/Application Support/CyberAudio'


def snapshot():
    with urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=5) as response:
        state = json.load(response)
    return {'instance': state['instance'], 'status': state['status'], 'busy': state['busy'],
            'config_sha256': sha(production / 'config/service.json'), 'token_sha256': sha(production / 'config/api-token')}


receipt = {'status': 'running', 'started': datetime.now(timezone.utc).isoformat(),
           'source_sha256': sha(Path(__file__)), 'production_before': snapshot(), 'model_loaded': False, 'steps': []}
assert receipt['production_before']['status'] == 'ready' and not receipt['production_before']['busy']
path = base / 'runner.json'
assert not path.exists()
began = time.monotonic()
process = None


def save():
    receipt['elapsed_seconds'] = time.monotonic() - began
    path.write_text(json.dumps(receipt, indent=2) + '\n')


def run(name, args, timeout=180, offline=False):
    global process
    environment = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false',
                       PYTHONDONTWRITEBYTECODE='1', HF_HOME=str(base / 'private-cache/huggingface'))
    step = {'name': name, 'args': args, 'status': 'running'}
    receipt['steps'].append(step); save()
    with (base / (name + '.txt')).open('xb') as log:
        process = subprocess.Popen(args, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        step['owned_group_pid'] = process.pid; save()
        process.wait(timeout=timeout)
    step.update(status='completed' if process.returncode == 0 else 'failed', exit_code=process.returncode); save()
    assert process.returncode == 0, name + ' failed; preserve original log'
    print(name, 'completed', round(time.monotonic() - began, 2), 'seconds', flush=True)
    process = None


def interrupted(*_):
    raise RuntimeError('Model-free Mel diagnostic interrupted')


signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)
save()
try:
    runtime = str(base.parent / 'reference-parity-20261107/runtime/bin/python')
    app = project / '.context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app'
    run('prepare', [runtime, str(base / 'prepare.py')])
    receipt['plan_sha256'] = sha(base / 'plan.json')
    run('reference-constants', ['/usr/bin/sandbox-exec', '-f', str(app / 'Contents/Resources/offline.sb'), runtime, str(base / 'reference-constants.py')])
    template = read(base.parent / 'asr-reference-20261107/front-runner.json')
    build = base / 'native-build'; build.mkdir(exist_ok=False)
    compile_args = [a.replace('ASRFrontProbe', 'MelDSPProbe') for a in template['steps'][0]['args']]
    compile_args[compile_args.index('-o') + 1] = str(build / 'MelDSPProbe.o')
    compile_args[-1] = str(base / 'MelDSPProbe.swift')
    run('compile', compile_args)
    link_args = list(template['steps'][1]['args'])
    list_index = next(i for i, a in enumerate(link_args) if a.startswith('@') and a.endswith('.LinkFileList'))
    lines = Path(link_args[list_index][1:]).read_text().splitlines()
    previous = str(base.parent / 'asr-reference-20261107/front-build/ASRFrontProbe.o')
    assert lines.count(previous) == 1
    filelist = build / 'Objects.LinkFileList'
    filelist.write_text('\n'.join(str(build / 'MelDSPProbe.o') if line == previous else line for line in lines) + '\n')
    link_args[list_index] = '@' + str(filelist)
    executable = build / 'mel-dsp-probe'
    link_args[link_args.index('-o') + 1] = str(executable)
    link_args = [a.replace('ASRFrontProbe', 'MelDSPProbe') for a in link_args]
    run('link', link_args)
    receipt['caller_sha256'] = sha(base / 'MelDSPProbe.swift')
    receipt['executable_sha256'] = sha(executable)
    run('native', ['/usr/bin/caffeinate', '-i', '/usr/bin/sandbox-exec', '-f', str(app / 'Contents/Resources/offline.sb'),
        str(executable), str(app / 'Contents/Resources/mlx.metallib'), str(base / 'plan.json'),
        str(base / 'reference-constants.safetensors'), str(base / 'native')], timeout=200)
    assert read(base / 'native/report.json')['status'] == 'diagnostic_completed'
    receipt['status'] = 'diagnostic_completed'
except Exception as error:
    receipt.update(status='failed', error_type=type(error).__name__, error=str(error))
finally:
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try: process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=15); receipt['forced_cleanup'] = True
    receipt['production_after'] = snapshot()
    receipt['production_preserved'] = receipt['production_before'] == receipt['production_after']
    receipt['protected_inputs_preserved'] = protected == {p: sha(p) for p in protected}
    if not receipt['production_preserved'] or not receipt['protected_inputs_preserved']: receipt['status'] = 'failed'
    receipt['finished'] = datetime.now(timezone.utc).isoformat(); save()
print(receipt['status'], round(receipt['elapsed_seconds'], 2), 'seconds')
raise SystemExit(0 if receipt['status'] == 'diagnostic_completed' else 1)
