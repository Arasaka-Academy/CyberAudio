"""Run native then reference serially, with offline reference and owned cleanup."""
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
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
plan = read(base / 'plan.json')
protected = read(base.parent / 'power-kernel-20261107/generation-build/build.json')['preserved_input_sha256']
assert protected == {p: sha(p) for p in protected}
production = Path.home() / 'Library/Application Support/CyberAudio'


def snapshot():
    with urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=5) as response:
        state = json.load(response)
    return {'instance': state['instance'], 'status': state['status'], 'busy': state['busy'],
            'config_sha256': sha(production / 'config/service.json'), 'token_sha256': sha(production / 'config/api-token')}


receipt = {'status': 'running', 'started': datetime.now(timezone.utc).isoformat(),
           'plan_sha256': sha(base / 'plan.json'), 'source_sha256': sha(base / 'run-front-reference.py'),
           'production_before': snapshot(), 'steps': []}
assert receipt['production_before']['status'] == 'ready' and not receipt['production_before']['busy']
path = base / 'front-reference-runner.json'
assert not path.exists()
process = None
began = time.monotonic()


def save():
    receipt['elapsed_seconds'] = time.monotonic() - began
    path.write_text(json.dumps(receipt, indent=2) + '\n')


def run(name, args, environment, timeout):
    global process
    step = {'name': name, 'args': args, 'status': 'running'}
    receipt['steps'].append(step)
    save()
    with (base / (name + '.txt')).open('xb') as log:
        process = subprocess.Popen(args, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        step['owned_group_pid'] = process.pid
        save()
        process.wait(timeout=timeout)
    step['exit_code'] = process.returncode
    step['status'] = 'completed' if process.returncode == 0 else 'failed'
    save()
    print(name, step['status'], round(time.monotonic() - began, 2), 'seconds', flush=True)
    assert process.returncode == 0, name + ' failed; retain original report/log'
    process = None


def interrupt(*_):
    raise RuntimeError('ASR front diagnostic interrupted')


signal.signal(signal.SIGTERM, interrupt)
signal.signal(signal.SIGINT, interrupt)
save()
try:
    app = project / '.context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app'
    runtime = base.parent / 'reference-parity-20261107/runtime/bin/python'
    assert read(base / 'front-runner.json')['status'] == 'diagnostic_completed'
    environment = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false',
                       HF_HOME=str(base / 'private-cache/huggingface'), XDG_CACHE_HOME=str(base / 'private-cache'),
                       PYTHONDONTWRITEBYTECODE='1')
    args = ['/usr/bin/caffeinate', '-i', '/usr/bin/sandbox-exec', '-f', str(app / 'Contents/Resources/offline.sb'),
            str(runtime), '-u', str(base / 'compare-front-reference.py')]
    run('front-reference', args, environment, 630)
    assert read(base / 'front-comparison.json')['status'] == 'diagnostic_completed'
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
            receipt['forced_cleanup'] = True
    receipt['production_after'] = snapshot()
    receipt['production_preserved'] = receipt['production_before'] == receipt['production_after']
    receipt['protected_inputs_preserved'] = protected == {p: sha(p) for p in protected}
    receipt['reference_sources_preserved'] = plan['source_sha256'] == {p: sha(project / p) for p in plan['source_sha256']}
    if not all(receipt[k] for k in ['production_preserved', 'protected_inputs_preserved', 'reference_sources_preserved']):
        receipt['status'] = 'failed'
    receipt['finished'] = datetime.now(timezone.utc).isoformat()
    save()
print(receipt['status'], round(receipt['elapsed_seconds'], 2), 'seconds')
raise SystemExit(0 if receipt['status'] == 'diagnostic_completed' else 1)
