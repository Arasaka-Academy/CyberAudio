"""Fresh, serialized baseline/candidate generations; change only Power dispatch."""
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

base = Path(__file__).resolve().parent
project = base.parents[2]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
scalar_plan = read(base / 'plan.json')
scalar_runner = read(base / 'runner.json')
assert scalar_runner['status'] == 'diagnostic_completed'
assert scalar_runner['production_preserved'] and scalar_runner['shared_inputs_preserved']
assert scalar_runner['plan_sha256'] == sha(base / 'plan.json')
prior = base.parent / 'sampling-parity-20261107'
caller = prior / 'OrderCandidateProbe.swift'
caller_object = prior / 'order-build/OrderCandidateProbe.o'
template = read(base.parent / 'm4-acceptance-20261106/seeded-tts-build/build.json')
baseline_path = base.parent / 'm4-acceptance-20261106/m1-text-layout-probe/report.json'
baseline = read(baseline_path)
root = base / 'generation-build'
root.mkdir(exist_ok=False)
original_list = Path(next(s[1:] for s in template['link_command'] if s.startswith('@') and s.endswith('.LinkFileList')))
original_lines = original_list.read_text().splitlines()
old_main = str(base.parent / 'm4-acceptance-20261106/seeded-tts-build/SeededTTSProbe.o')
assert original_lines.count(old_main) == 1
lines = [str(caller_object) if line == old_main else line for line in original_lines]
original_power = next(p for p in scalar_plan['preserved_input_sha256'] if p.endswith('/metal/jit_kernels.cpp.o'))
assert lines.count(original_power) == 1
private_power = base / 'native-build/jit_kernels.cpp.o'
protected = {str(Path(shlex.split(line)[0])): sha(shlex.split(line)[0]) for line in lines}
protected.update(scalar_plan['preserved_input_sha256'])
protected[str(caller)] = sha(caller)
assert protected == {p: sha(p) for p in protected}
app = project / '.context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app'
shader = app / 'Contents/Resources/mlx.metallib'
assert sha(shader) == scalar_plan['shader_sha256']
cases = read(prior / 'order-plan.json')['cases']
plan = {
    'purpose': 'Fresh original managed-sampler control versus only float32 Power JIT-to-precompiled dispatch; no sampler-order candidate combined',
    'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    'cases': cases, 'temperature': 0.7, 'subtalker_temperature': 0.7,
    'top_k': 50, 'top_p': 1.0, 'repetition_penalty': 1.05,
    'max_tokens': 1200, 'total_budget_seconds': 600, 'individual_budget_seconds': 120,
    'scalar_plan_sha256': sha(base / 'plan.json'),
    'baseline_report_sha256': sha(baseline_path),
    'caller_source_sha256': sha(caller), 'caller_object_sha256': sha(caller_object),
    'private_power_object_sha256': sha(private_power),
    'shader_sha256': sha(shader),
    'qualification': [
        'All five historical original-prefill token files and PCM digests must match the fresh original-JIT control before starting the candidate',
        'Retain all twelve outputs, including generation-limit failures',
        'Both fixed seed196 repeats must have identical tokens and PCM in each arm',
        'Same final-App ASR and fixed broad cohort required before any adoption',
        'No production source/default changes or strict-soak reruns based only on this small cohort']}
plan_path = base / 'generation-plan.json'
assert not plan_path.exists()
plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
build = {'status': 'building', 'plan_sha256': sha(plan_path),
         'preserved_input_sha256': protected, 'arms': {}}
receipt_path = base / 'generation-runner.json'
assert not receipt_path.exists()
production = Path.home() / 'Library/Application Support/CyberAudio'


def snapshot():
    with urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=5) as response:
        state = json.load(response)
    return {'instance': state['instance'], 'status': state['status'], 'busy': state['busy'],
            'config_sha256': sha(production / 'config/service.json'), 'token_sha256': sha(production / 'config/api-token')}


receipt = {'status': 'running', 'started': datetime.now(timezone.utc).isoformat(),
           'production_before': snapshot(), 'plan_sha256': sha(plan_path),
           'source_sha256': sha(base / 'run-generations.py'), 'steps': []}
assert receipt['production_before'] == scalar_runner['production_after']
assert receipt['production_before']['status'] == 'ready' and not receipt['production_before']['busy']
process = None
began = time.monotonic()


def save():
    receipt['elapsed_seconds'] = time.monotonic() - began
    temporary = receipt_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(receipt, indent=2) + '\n')
    temporary.replace(receipt_path)
    (root / 'build.json').write_text(json.dumps(build, indent=2) + '\n')


def run(name, command, timeout):
    global process
    step = {'name': name, 'args': command, 'status': 'running'}
    receipt['steps'].append(step)
    save()
    with (root / (name + '.txt')).open('xb') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        step['owned_group_pid'] = process.pid
        step['owned_identity'] = subprocess.check_output(['/bin/ps', '-p', str(process.pid), '-o', 'pid=,lstart=,command='], text=True).strip()
        save()
        process.wait(timeout=timeout)
    step['exit_code'] = process.returncode
    step['status'] = 'completed' if process.returncode == 0 else 'failed'
    save()
    print(name, step['status'], round(time.monotonic() - began, 2), flush=True)
    assert process.returncode == 0, name + ' failed; retained log'
    process = None


def interrupted(*_):
    raise RuntimeError('Generation runner interrupted')


signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)
save()
try:
    for label in ['original-jit', 'precompiled-power']:
        selected = [str(private_power) if label == 'precompiled-power' and line == original_power else line for line in lines]
        filelist = root / (label + '.LinkFileList')
        filelist.write_text('\n'.join(selected) + '\n')
        executable = root / label
        args = [a.replace('SeededTTSProbe', 'OrderCandidateProbe') for a in template['link_command']]
        args[args.index('-o') + 1] = str(executable)
        args[next(i for i, s in enumerate(args) if s.startswith('@') and s.endswith('.LinkFileList'))] = '@' + str(filelist)
        build['arms'][label] = {'link_command': args, 'link_file_list_sha256': sha(filelist)}
        run(label + '-link', args, 180)
        build['arms'][label]['executable_sha256'] = sha(executable)
    build['status'] = 'built'
    save()
    for label in ['original-jit', 'precompiled-power']:
        out = base / ('generation-' + label)
        args = ['/usr/bin/caffeinate', '-i', '/usr/bin/sandbox-exec', '-f', str(app / 'Contents/Resources/offline.sb'),
                str(root / label), str((project / '.context/robustness/linked-models/tts').resolve()),
                str(shader), str(out), str(plan_path), str(baseline_path)]
        run(label + '-generation', args, plan['total_budget_seconds'] + 40)
        report = read(out / 'report.json')
        assert report['status'] == 'diagnostic_completed' and report['plan_sha256'] == sha(plan_path)
        assert len(report['results']) == 6 and report['failure_repeat_tokens_exact'] and report['failure_repeat_pcm_exact']
        for row, case in zip(report['results'], cases, strict=True):
            assert row['case'] == case and row['pcm_sha256'] == sha(out / row['pcm_file'])
        if label == 'original-jit':
            controls = []
            for old, row in zip([r for r in baseline['results'] if r['mode'] == 'prefill'], report['results'][:5], strict=True):
                tokens = read(baseline_path.parent / (old['id'] + '-tokens.json'))
                assert row['tokens'] == tokens
                assert row['pcm_sha256'] == old['pcm_sha256']
                assert row['token_count'] == old['token_count'] and row['eos_seen'] == old['eos_seen']
                controls.append({'id': row['id'], 'historical_id': old['id'], 'tokens_exact': True, 'pcm_exact': True})
            receipt['historical_control_parity'] = controls
        receipt[label + '_report_sha256'] = sha(out / 'report.json')
        save()
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
    receipt['production_preserved'] = receipt['production_after'] == receipt['production_before']
    receipt['shared_inputs_preserved'] = protected == {p: sha(p) for p in protected}
    if not receipt['production_preserved'] or not receipt['shared_inputs_preserved']:
        receipt['status'] = 'failed'
    receipt['finished'] = datetime.now(timezone.utc).isoformat()
    save()
print(receipt['status'], round(receipt['elapsed_seconds'], 2), 'seconds')
raise SystemExit(0 if receipt['status'] == 'diagnostic_completed' else 1)
