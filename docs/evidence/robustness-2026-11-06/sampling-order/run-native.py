"""Bounded native or reference sampler diagnostics with production preservation."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

base = Path(__file__).resolve().parent
project = base.parents[2]
stage = sys.argv[1]
assert stage in {'native', 'reference'}
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
build = json.loads((base / 'native-build/build.json').read_text())
assert build['status'] == 'built' and build['shared_and_production_inputs_preserved']
assert sha(base / 'plan.json') == build['plan_sha256']
production = Path.home() / 'Library/Application Support/CyberAudio'
path = base / f'{stage}-runner.json'
assert not path.exists()

def snapshot():
    with urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=5) as response:
        state = json.load(response)
    return {'instance': state['instance'], 'status': state['status'], 'busy': state['busy'],
            'config_sha256': sha(production / 'config/service.json'),
            'token_sha256': sha(production / 'config/api-token')}

receipt = {'status': 'running', 'stage': stage, 'started': datetime.now(timezone.utc).isoformat(),
           'production_before': snapshot(), 'plan_sha256': sha(base / 'plan.json')}
assert receipt['production_before']['status'] == 'ready' and not receipt['production_before']['busy']
began = time.monotonic()
process = None
def save():
    receipt['elapsed_seconds'] = time.monotonic() - began
    path.write_text(json.dumps(receipt, indent=2) + '\n')
def interrupted(*_): raise RuntimeError('Sampler runner interrupted')
signal.signal(signal.SIGINT, interrupted)
signal.signal(signal.SIGTERM, interrupted)
save()
try:
    app = project / '.context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app'
    prefix = ['/usr/bin/caffeinate', '-i', '/usr/bin/sandbox-exec', '-f', str(app / 'Contents/Resources/offline.sb')]
    if stage == 'native':
        executable = base / 'native-build/sampling-parity-probe'
        assert sha(executable) == build['executable_sha256']
        receipt['executable_sha256'] = sha(executable)
        command = prefix + [str(executable), str(base.parent / 'reference-parity-20261107/native-capture'),
            str(app / 'Contents/Resources/mlx.metallib'),
            str((project / '.context/robustness/linked-models/tts/config.json').resolve()), str(base / 'native-filters')]
        report_path = base / 'native-filters/report.json'
    else:
        source = base / 'compare.py'
        receipt['source_sha256'] = sha(source)
        command = prefix + [str(base.parent / 'reference-parity-20261107/runtime/bin/python'), str(source)]
        report_path = base / 'comparison.json'
    environment = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false',
                       HF_HOME=str(base / 'private-cache/huggingface'), XDG_CACHE_HOME=str(base / 'private-cache'),
                       PYTHONDONTWRITEBYTECODE='1')
    with (base / f'{stage}.log').open('xb') as log:
        process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        receipt['owned_group_pid'] = process.pid
        receipt['owned_identity'] = subprocess.check_output(['/bin/ps', '-p', str(process.pid), '-o', 'pid=,lstart=,command='], text=True).strip()
        save()
        process.wait(timeout=140)
    receipt['exit_code'] = process.returncode
    assert process.returncode == 0, f'{stage} failed; inspect retained log'
    report = json.loads(report_path.read_text())
    assert report['status'] == 'diagnostic_completed'
    receipt['status'] = 'completed'
except Exception as error:
    receipt.update(status='failed', error_type=type(error).__name__, error=str(error))
finally:
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try: process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)
            receipt['forced_cleanup'] = True
    receipt['owned_process_exit_code'] = process.returncode if process else None
    try:
        receipt['production_after'] = snapshot()
        receipt['production_preserved'] = receipt['production_after'] == receipt['production_before']
        assert receipt['production_preserved']
        assert build['preserved_input_sha256'] == {p: sha(p) for p in build['preserved_input_sha256']}
        receipt['sdk_and_helpers_preserved'] = True
    except Exception as error: receipt.update(status='failed', preservation_error=str(error))
    receipt['finished'] = datetime.now(timezone.utc).isoformat()
    save()
print(stage, receipt['status'], round(receipt['elapsed_seconds'], 2), 'seconds')
raise SystemExit(0 if receipt['status'] == 'completed' else 1)
