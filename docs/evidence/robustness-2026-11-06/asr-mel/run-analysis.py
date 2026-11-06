"""Bounded model-free reference analysis, with production and source snapshots."""
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
assert read(base / 'runner.json')['status'] == 'diagnostic_completed'
protected = read(base.parent / 'power-kernel-20261107/generation-build/build.json')['preserved_input_sha256']
production = Path.home() / 'Library/Application Support/CyberAudio'


def snapshot():
    with urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=5) as response:
        state = json.load(response)
    return {'instance': state['instance'], 'status': state['status'], 'busy': state['busy'],
            'config_sha256': sha(production / 'config/service.json'), 'token_sha256': sha(production / 'config/api-token')}


receipt = {'status': 'running', 'started': datetime.now(timezone.utc).isoformat(), 'model_loaded': False,
           'source_sha256': sha(Path(__file__)), 'analysis_source_sha256': sha(base / 'analyze.py'),
           'plan_sha256': sha(base / 'plan.json'), 'production_before': snapshot()}
assert receipt['production_before'] == read(base / 'runner.json')['production_after']
path = base / 'analysis-runner.json'
assert not path.exists()
process = None
began = time.monotonic()


def save():
    receipt['elapsed_seconds'] = time.monotonic() - began
    path.write_text(json.dumps(receipt, indent=2) + '\n')


def interrupted(*_):
    raise RuntimeError('Mel analysis interrupted')


signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)
save()
try:
    app = project / '.context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app'
    args = ['/usr/bin/caffeinate', '-i', '/usr/bin/sandbox-exec', '-f', str(app / 'Contents/Resources/offline.sb'),
            str(base.parent / 'reference-parity-20261107/runtime/bin/python'), '-u', str(base / 'analyze.py')]
    receipt['args'] = args
    environment = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false', PYTHONDONTWRITEBYTECODE='1')
    with (base / 'analysis.txt').open('xb') as log:
        process = subprocess.Popen(args, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        receipt['owned_group_pid'] = process.pid; save()
        process.wait(timeout=200)
    receipt['exit_code'] = process.returncode
    assert process.returncode == 0 and read(base / 'comparison.json')['status'] == 'diagnostic_completed'
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
