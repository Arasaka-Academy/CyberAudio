"""Run the declared current-route baselines and candidates sequentially, offline."""
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
build = json.loads((base / 'matrix-build/build.json').read_text())
plan = json.loads((base / 'matrix-plan.json').read_text())
assert build['status'] == 'built' and build['shared_and_production_inputs_preserved']
assert sha(base / 'matrix-plan.json') == build['plan_sha256']
asr = json.loads((base / 'order-roundtrip/reports/asr-followup.json').read_text())
assert asr['status'] == 'diagnostic_completed' and asr['owned_helper_exit_code'] == 0
assert asr['production_and_config_preserved'] and asr['temporary_request_cleanup']
assert len(asr['results']) == asr['expected_requests'] == 18
production = Path.home() / 'Library/Application Support/CyberAudio'
receipt_path = base / 'matrix-runner.json'
assert not receipt_path.exists()

def snapshot():
    with urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=5) as response:
        state = json.load(response)
    return {'instance': state['instance'], 'status': state['status'], 'busy': state['busy'],
            'config_sha256': sha(production / 'config/service.json'), 'token_sha256': sha(production / 'config/api-token')}

receipt = {'status': 'running', 'started': datetime.now(timezone.utc).isoformat(), 'stages': [],
           'plan_sha256': sha(base / 'matrix-plan.json'), 'build_sha256': sha(base / 'matrix-build/build.json'),
           'production_before': snapshot()}
assert receipt['production_before']['status'] == 'ready' and not receipt['production_before']['busy']
started = time.monotonic()
process = None
def save():
    receipt['elapsed_seconds'] = time.monotonic()-started
    temporary = receipt_path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(receipt, indent=2)+'\n')
    temporary.replace(receipt_path)
def interrupted(*_): raise RuntimeError('Matrix runner interrupted')
signal.signal(signal.SIGINT, interrupted)
signal.signal(signal.SIGTERM, interrupted)
save()
try:
    app = project / '.context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app'
    for stage in ['baseline', 'candidate']:
        executable = base / 'matrix-build' / (stage + '-matrix-probe')
        assert sha(executable) == build['executables'][stage]['sha256']
        root = base / ('matrix-' + stage)
        assert not root.exists()
        row = {'stage': stage, 'status': 'running', 'started': datetime.now(timezone.utc).isoformat()}
        receipt['stages'].append(row)
        command = ['/usr/bin/caffeinate', '-i', '/usr/bin/sandbox-exec', '-f', str(app / 'Contents/Resources/offline.sb'),
                   str(executable), str((project / '.context/robustness/linked-models/tts').resolve()),
                   str(app / 'Contents/Resources/mlx.metallib'), str(root), str(base / 'matrix-plan.json'),
                   str(base / 'order-generations/report.json'), stage]
        with (base / ('matrix-' + stage + '.log')).open('xb') as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            row['owned_group_pid'] = process.pid
            row['owned_identity'] = subprocess.check_output(['/bin/ps', '-p', str(process.pid), '-o', 'pid=,lstart=,command='], text=True).strip()
            save()
            process.wait(timeout=plan['per_stage_budget_seconds']+20)
        row['exit_code'] = process.returncode
        assert process.returncode == 0, f'{stage} failed; inspect retained report/log'
        report = json.loads((root / 'report.json').read_text())
        expected = plan['expected_candidates' if stage == 'candidate' else 'expected_fresh_baselines']
        assert report['status'] == 'diagnostic_completed' and len(report['results']) == expected
        assert report['plan_sha256'] == build['plan_sha256']
        if stage == 'candidate': assert report['preliminary_candidate_parity']
        row.update(status='completed', finished=datetime.now(timezone.utc).isoformat(),
                   completed_generations=report['completed_generations'], report_sha256=sha(root / 'report.json'))
        save()
        print('STAGE', stage, 'completed', expected, flush=True)
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
    receipt['last_owned_process_exit_code'] = process.returncode if process else None
    try:
        receipt['production_after'] = snapshot()
        receipt['production_preserved'] = receipt['production_after'] == receipt['production_before']
        assert receipt['production_preserved']
        assert build['preserved_input_sha256'] == {p: sha(p) for p in build['preserved_input_sha256']}
        receipt['sdk_and_helpers_preserved'] = True
    except Exception as error: receipt.update(status='failed', preservation_error=str(error))
    receipt['finished'] = datetime.now(timezone.utc).isoformat()
    save()
print(receipt['status'], round(receipt['elapsed_seconds'],2), 'seconds', flush=True)
raise SystemExit(0 if receipt['status'] == 'completed' else 1)
