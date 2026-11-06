"""Bound one offline reference workload and retain process/preservation receipts."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import signal
import socket
import subprocess
import time
import urllib.request

base = Path(__file__).resolve().parent
project = base.parents[2]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
plan = read(base / 'decoder-followup-plan.json')
prior = read(base / 'runner.json')
asr = read(base / 'reference-roundtrip/reports/asr-followup.json')
assert prior['status'] == 'completed' and asr['status'] == 'diagnostic_completed' and asr['owned_helper_exit_code'] == 0
with socket.socket() as connection:
    assert connection.connect_ex(('127.0.0.1', 18890)) != 0
build = read(base / 'decoder-build/build.json')
assert build['status'] == 'built' and build['shared_and_production_inputs_preserved']
inputs = build['preserved_input_sha256']
assert inputs == {p: sha(p) for p in inputs}
production = Path.home() / 'Library/Application Support/CyberAudio'
path = base / 'decoder-native-runner.json'
assert not path.exists()


def snapshot():
    with urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=5) as response:
        state = json.load(response)
    return {'instance': state['instance'], 'status': state['status'], 'busy': state['busy'],
            'config_sha256': sha(production / 'config/service.json'), 'token_sha256': sha(production / 'config/api-token')}


receipt = {'status': 'running', 'started': datetime.now(timezone.utc).isoformat(),
           'production_before': snapshot(), 'source_sha256': sha(base / 'ReferenceDecodeProbe.swift'),
           'plan_sha256': sha(base / 'decoder-followup-plan.json')}
start = time.monotonic()
process = None


def save():
    receipt['elapsed_seconds'] = time.monotonic() - start
    path.write_text(json.dumps(receipt, indent=2) + '\n')


def interrupted(*_):
    raise RuntimeError('Reference workload interrupted')


signal.signal(signal.SIGINT, interrupted)
signal.signal(signal.SIGTERM, interrupted)
save()
try:
    app = project / '.context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app'
    environment = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false',
                       HF_HOME=str(base / 'private-cache/huggingface'), XDG_CACHE_HOME=str(base / 'private-cache'),
                       PYTHONDONTWRITEBYTECODE='1')
    args = ['/usr/bin/caffeinate', '-i', '/usr/bin/sandbox-exec', '-f', str(app / 'Contents/Resources/offline.sb'),
            str(base / 'decoder-build/reference-decode-probe'),
            str((project / '.context/robustness/linked-models/tts').resolve()),
            str(app / 'Contents/Resources/mlx.metallib'), str(base / 'decoder-native'), str(base / 'decoder-followup-plan.json')]
    receipt['command'] = args
    with (base / 'decoder-native.log').open('xb') as log:
        process = subprocess.Popen(args, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        receipt['owned_group_pid'] = process.pid
        receipt['owned_identity'] = subprocess.check_output(['/bin/ps', '-p', str(process.pid), '-o', 'pid=,lstart=,command='], text=True).strip()
        save()
        process.wait(timeout=plan['total_budget_seconds'] + 20)
    receipt['exit_code'] = process.returncode
    assert process.returncode == 0, 'Native decoder failed; original report/log retained'
    report = read(base / 'decoder-native/report.json')
    assert report['status'] == 'diagnostic_completed' and len(report['results']) == len(plan['cases'])
    receipt['status'] = 'completed'
except Exception as error:
    receipt.update(status='failed', error_type=type(error).__name__, error=str(error))
finally:
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=12)
            receipt['forced_cleanup'] = True
    receipt['owned_process_exit_code'] = process.returncode if process else None
    try:
        receipt['production_after'] = snapshot()
        assert receipt['production_after'] == receipt['production_before']
        receipt['production_preserved'] = True
        assert inputs == {p: sha(p) for p in inputs}
        receipt['sdk_and_helpers_preserved'] = True
    except Exception as error:
        receipt.update(status='failed', preservation_error=str(error))
    receipt['finished'] = datetime.now(timezone.utc).isoformat()
    save()
print(receipt['status'], round(receipt['elapsed_seconds'], 2), 'seconds')
raise SystemExit(0 if receipt['status'] == 'completed' else 1)
