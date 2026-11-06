"""Run one frozen segmentation pilot; preserve all failures, never restart it."""
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
    return {'instance':state['instance'], 'status':state['status'], 'busy':state['busy'],
            'config_sha256':sha(production/'config/service.json'), 'token_sha256':sha(production/'config/api-token')}


receipt = {'status':'running', 'started':datetime.now(timezone.utc).isoformat(),
           'source_sha256':sha(Path(__file__)), 'production_before':snapshot(), 'steps':[]}
assert receipt['production_before']['status'] == 'ready' and not receipt['production_before']['busy']
path = base/'runner.json'; assert not path.exists()
process = None
began = time.monotonic()


def save():
    receipt['elapsed_seconds'] = time.monotonic()-began
    path.write_text(json.dumps(receipt,indent=2)+'\n')


def run(name, args, timeout):
    global process
    step = {'name':name, 'args':args, 'status':'running'}
    receipt['steps'].append(step); save()
    with (base/(name+'.txt')).open('xb') as log:
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        step['owned_group_pid'] = process.pid; save()
        process.wait(timeout=timeout)
    step.update(status='completed' if process.returncode==0 else 'failed',exit_code=process.returncode); save()
    assert process.returncode==0, name+' failed; preserve original receipt/log'
    print(name,'completed',round(time.monotonic()-began,2),'seconds',flush=True)
    process = None


def interrupted(*_):
    raise RuntimeError('Segmentation pilot interrupted')


signal.signal(signal.SIGTERM,interrupted); signal.signal(signal.SIGINT,interrupted)
save()
try:
    runtime = str(base.parent/'reference-parity-20261107/runtime/bin/python')
    run('prepare',[runtime,str(base/'prepare.py')],30)
    receipt['plan_sha256'] = sha(base/'plan.json')
    run('prepare-runner',[runtime,str(base/'prepare-runner.py')],30)
    processes = subprocess.check_output(['/bin/ps','-axo','pid=,pgid=,command='],text=True).splitlines()
    for oldroot, names in [('asr-reference-20261107',['runner.json','front-runner.json','front-reference-runner.json','injection-runner.json']),
                           ('asr-mel-20261107',['runner.json','analysis-runner.json'])]:
        for name in names:
            old = read(base.parent/oldroot/name)
            owned = [str(s['owned_group_pid']) for s in old.get('steps',[])]
            if 'owned_group_pid' in old: owned.append(str(old['owned_group_pid']))
            assert not any(pid in line.split()[:2] for pid in owned for line in processes)
    run('native-http',[runtime,'-u',str(base/'run-native.py')],630)
    assert read(base/'native-http/reports/asr-followup.json')['status']=='diagnostic_completed'
    receipt['status'] = 'diagnostic_completed'
except Exception as error:
    receipt.update(status='failed',error_type=type(error).__name__,error=str(error))
finally:
    if process is not None and process.poll() is None:
        os.killpg(process.pid,signal.SIGTERM)
        try: process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL); process.wait(timeout=15); receipt['forced_cleanup']=True
    receipt['production_after'] = snapshot()
    receipt['production_preserved'] = receipt['production_before']==receipt['production_after']
    receipt['protected_inputs_preserved'] = protected=={p:sha(p) for p in protected}
    if not receipt['production_preserved'] or not receipt['protected_inputs_preserved']: receipt['status']='failed'
    receipt['finished'] = datetime.now(timezone.utc).isoformat(); save()
print(receipt['status'],round(receipt['elapsed_seconds'],2),'seconds')
raise SystemExit(0 if receipt['status']=='diagnostic_completed' else 1)
