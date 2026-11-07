"""One bounded resume through the successful task-owned loopback forward."""
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

root = Path('/Users/zhubby/Library/Caches/CyberAudio/RobustnessValidation/20261106-e2f34df-target1')
folder = root / 'public-download-refresh-20261107/full-download'
data = root / 'fresh-download'
artifact = root / 'artifacts/download-adaptive-candidate'
helper = artifact / 'CyberAudioAssets'
production = Path.home() / 'Library/Application Support/CyberAudio'
expected_helper = '06698db17958ab5adc2da7d1a1d9b23cbc34c57b67e1b1fb5cf7e9fd0e7adb1c'
maximum_seconds = 3600
reserve_bytes = 3 * 1024 ** 3


def sha(path, limit=None):
    digest = hashlib.sha256()
    remaining = limit
    with Path(path).open('rb') as stream:
        while remaining is None or remaining > 0:
            chunk = stream.read(min(8 * 1024 ** 2, remaining) if remaining is not None else 8 * 1024 ** 2)
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    if limit is not None:
        assert remaining == 0, 'Retained prefix unexpectedly shortened'
    return digest.hexdigest()


def snapshot():
    with urllib.request.urlopen('http://127.0.0.1:8765/ready', timeout=5) as response:
        state = json.load(response)
    return {'instance': state['instance'], 'status': state['status'], 'busy': state['busy'],
            'config_sha256': sha(production / 'config/service.json'),
            'token_sha256': sha(production / 'config/api-token')}


def inventory():
    return {str(path.relative_to(data)): {'bytes': path.stat().st_size, 'mtime_ns': path.stat().st_mtime_ns}
            for path in data.rglob('*') if path.is_file()}


def cli(command):
    run = subprocess.run([str(helper), command, '--root', str(data), '--json', 'true'],
                         capture_output=True, text=True, timeout=120)
    (folder / (command + '.stdout.private')).write_text(run.stdout)
    (folder / (command + '.stderr.private')).write_text(run.stderr)
    assert run.returncode == 0, 'Assets status/verification failed'
    return json.loads(run.stdout)


folder.mkdir(mode=0o700, exist_ok=False)
began = time.monotonic()
report = {'status': 'preflight', 'started_at': datetime.now(timezone.utc).isoformat(),
          'maximum_seconds': maximum_seconds, 'disk_reserve_bytes': reserve_bytes,
          'runner_pid': os.getpid(), 'runner_sha256': sha(Path(__file__)),
          'proxy': 'http://127.0.0.1:17890', 'tls_validation': 'default',
          'scope': 'One real Assets download resume using retained partials; no inference or App install',
          'model_loaded': False, 'system_proxy_changed': False}
process = None
idle = None
cancelled = False
configuration = None
assets_before = None
prefixes = {}
initial_finals = {}


def save():
    report['elapsed_seconds'] = time.monotonic() - began
    temporary = folder / 'result.json.tmp'
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(folder / 'result.json')


def stop(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=15)
            report['forced_cleanup'] = True


def interrupt(*_):
    global cancelled
    cancelled = True


def progress():
    current = inventory()
    partial_bytes = sum(row['bytes'] for name, row in current.items() if name.endswith('.partial'))
    present = []
    for row in files:
        component, name = row['path'].split('/', 1)
        path = Path(config[component + '_dir']) / name
        if path.is_file():
            present.append(path.stat().st_size)
    return {'partial_bytes': partial_bytes, 'present_locked_file_count': len(present),
            'present_locked_bytes': sum(present)}


signal.signal(signal.SIGTERM, interrupt)
signal.signal(signal.SIGINT, interrupt)
signal.signal(signal.SIGHUP, interrupt)
save()
try:
    assert json.loads((root / '.acceptance-owner').read_text())['source_commit'] == 'e2f34df30eab49e526a3dfd2fa7d6fffbfb81d0b'
    probe = json.loads((root / 'public-download-refresh-20261107/tunnel-range/result.json').read_text())
    assert probe['normal_tls_correct_range_passed'] and probe['production_preserved'] and probe['assets_preserved']
    with socket.create_connection(('127.0.0.1', 17890), timeout=3):
        pass
    rows = subprocess.check_output(['/bin/ps', '-axo', 'pid=,ppid=,lstart=,command='], text=True)
    assert not any(str(data) in row and '/CyberAudioAssets download ' in row for row in rows.splitlines()), 'Matching download child already live'
    manifest = json.loads((artifact / 'manifest.json').read_text())
    for name, digest in manifest['files'].items():
        assert sha(artifact / name) == digest, 'Candidate artifact changed'
    assert sha(helper) == expected_helper
    signed = subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(helper)], capture_output=True, text=True, timeout=30)
    assert signed.returncode == 0, 'Candidate signature verification failed'
    assert sha(artifact / 'CyberAudioNative_CyberAudioCore.bundle/Contents/Resources/Models.lock.json') == sha(root / 'Models.lock.json')
    report.update(helper_sha256=sha(helper), artifact_manifest=manifest, signature_exit_code=signed.returncode,
                  models_lock_sha256=sha(root / 'Models.lock.json'), production_before=snapshot())
    assert report['production_before']['status'] == 'ready' and not report['production_before']['busy']
    assert report['production_before'] == probe['production_after'], 'Production changed since the successful route probe'
    configuration = {name: sha(data / 'config' / name) for name in ['service.json', 'api-token']}
    report['configuration_before'] = configuration
    assets_before = inventory()
    report['assets_before'] = assets_before
    config = json.loads((data / 'config/service.json').read_text())
    files = json.loads((root / 'Models.lock.json').read_text())['files']
    assert len(files) == 23 and sum(row['size'] for row in files) == 5547997530
    for row in files:
        component, name = row['path'].split('/', 1)
        path = Path(config[component + '_dir']) / name
        assert data.resolve() in path.resolve().parents and not path.is_symlink(), 'Assets must remain in isolated root'
        if path.is_file():
            assert path.stat().st_size == row['size'] and sha(path) == row['sha256'], 'Existing final file invalid'
            initial_finals[str(path.relative_to(data))] = {'sha256': row['sha256'], 'bytes': row['size'], 'mtime_ns': path.stat().st_mtime_ns}
    for path in data.rglob('*.partial'):
        assert not path.is_symlink()
        matching = [row for row in files if path.stem == row['sha256']]
        assert len(matching) == 1, 'Partial must match a pinned asset'
        component, name = matching[0]['path'].split('/', 1)
        final_path = Path(config[component + '_dir']) / name
        prefixes[str(path.relative_to(data))] = {'bytes': path.stat().st_size, 'sha256': sha(path),
                                                'final_path': str(final_path.relative_to(data))}
    report.update(initial_final_files=initial_finals, retained_prefixes=prefixes, before=cli('status'))
    assert report['before']['can_download'] and not report['before']['invalid_files'] and not report['before']['invalid_partials']
    assert report['before']['verified_bytes'] == 2469536771 and report['before']['resumable_bytes'] == 117440512
    assert os.statvfs(data).f_bavail * os.statvfs(data).f_frsize > reserve_bytes
    idle = subprocess.Popen(['/usr/bin/caffeinate', '-i', '-w', str(os.getpid())])
    with (folder / 'download.ndjson').open('xb') as output, (folder / 'download.stderr.private').open('xb') as errors:
        process = subprocess.Popen([str(helper), 'download', '--root', str(data), '--proxy', 'http://127.0.0.1:17890',
                                    '--parent-pid', str(os.getpid()), '--json', 'true'], stdout=output, stderr=errors)
        report.update(status='running', child_pid=process.pid, child_start_identity=subprocess.check_output(
            ['/bin/ps', '-p', str(process.pid), '-o', 'pid=,ppid=,lstart=,command='], text=True).strip())
        save()
        print(json.dumps({key: report[key] for key in ['status', 'runner_pid', 'child_pid', 'started_at', 'maximum_seconds']}), flush=True)
        next_progress = time.monotonic()
        while process.poll() is None:
            assert not cancelled, 'Runner cancelled'
            assert time.monotonic() - began < maximum_seconds, 'Actual download time budget reached'
            fs = os.statvfs(data)
            assert fs.f_bavail * fs.f_frsize > reserve_bytes, 'Disk reserve below 3 GiB'
            if time.monotonic() >= next_progress:
                report['progress'] = progress()
                save()
                print(json.dumps({'event': 'progress', 'elapsed_seconds': round(report['elapsed_seconds'], 1), **report['progress']}), flush=True)
                next_progress = time.monotonic() + 30
            time.sleep(1)
        report['exit_code'] = process.returncode
    report['final_status'] = cli('status')
    assert process.returncode == 0 and report['final_status']['models_ready'], 'Actual download did not complete'
    report['verification'] = cli('verify')
    verified = []
    for row in files:
        component, name = row['path'].split('/', 1)
        path = Path(config[component + '_dir']) / name
        assert data.resolve() in path.resolve().parents and not path.is_symlink()
        assert path.stat().st_size == row['size'] and sha(path) == row['sha256'], 'Independent asset validation failed'
        verified.append(row)
    report.update(status='passed', independently_verified_files=verified, independently_verified_bytes=5547997530)
except Exception as error:
    report.update(status='cancelled' if cancelled else 'failed', error_type=type(error).__name__, error=str(error))
finally:
    if process is not None:
        stop(process)
        report['child_final_exit_code'] = process.returncode
    if idle is not None:
        stop(idle)
    try:
        report['assets_after'] = inventory()
        if 'production_before' in report:
            report['production_after'] = snapshot()
            report['production_preserved'] = report['production_before'] == report['production_after']
            assert report['production_preserved'], 'Production state changed'
        if configuration is not None:
            report['configuration_token_preserved'] = configuration == {name: sha(data / 'config' / name) for name in configuration}
            assert report['configuration_token_preserved'], 'Validation configuration changed'
        report['initial_finals_preserved'] = all(
            (data / name).is_file() and sha(data / name) == row['sha256'] and
            (data / name).stat().st_mtime_ns == row['mtime_ns'] for name, row in initial_finals.items())
        assert report['initial_finals_preserved'], 'Existing final asset changed'
        preserved = []
        for name, row in prefixes.items():
            path = data / name
            if not path.is_file():
                path = data / row['final_path']
            assert path.is_file() and sha(path, row['bytes']) == row['sha256'], 'Retained prefix changed'
            preserved.append(name)
        report['prefixes_preserved'] = len(preserved) == len(prefixes)
        final_processes = subprocess.check_output(['/bin/ps', '-axo', 'pid=,ppid=,lstart=,command='], text=True)
        report['matching_download_processes_after'] = [row.strip() for row in final_processes.splitlines()
            if str(data) in row and '/CyberAudioAssets download ' in row]
        assert not report['matching_download_processes_after'], 'Download child remained live'
    except Exception as error:
        report.update(status='failed', preservation_error_type=type(error).__name__, preservation_error=str(error))
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    save()
    print(json.dumps({key: value for key, value in report.items() if key not in ['assets_before', 'assets_after', 'artifact_manifest', 'verification']}), flush=True)
raise SystemExit(0 if report['status'] == 'passed' else 1)
