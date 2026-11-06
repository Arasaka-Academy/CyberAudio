"""Read live identity and checkpoints; never infer life from a stale JSON status."""
from pathlib import Path
import json
import subprocess

base = Path(__file__).resolve().parent
script = r'''
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, subprocess, urllib.request
root=Path('/Users/zhubby/Library/Caches/CyberAudio/RobustnessValidation/20261106-e2f34df-target1')
rows=subprocess.run(['/bin/ps','-axo','pid=,ppid=,lstart=,command='],capture_output=True,text=True,check=True).stdout.splitlines()
result={'at':datetime.now(timezone.utc).isoformat(), 'processes':[r.strip() for r in rows if str(root) in r and 'python3 -' not in r]}
for name in ['soak-r2-result.json','data-soak-r2/reports/soak.json','data-soak-r2/reports/native-service.json','download-r2-result.json','download-retry-result.json']:
 path=root/name
 if path.is_file(): result[name]=json.loads(path.read_text())
data=root/'fresh-download'
result['partial_files']=[{'name':str(p.relative_to(data)),'bytes':p.stat().st_size} for p in data.rglob('*.partial')]
config=json.loads((data/'config/service.json').read_text())
files=json.loads((root/'Models.lock.json').read_text())['files']
final=[]
for row in files:
 component, name=row['path'].split('/',1)
 path=Path(config[component+'_dir'])/name
 if path.is_file(): final.append({'path':row['path'],'bytes':path.stat().st_size,'expected_bytes':row['size']})
result['final_files']=final
production=Path.home()/'Library/Application Support/CyberAudio'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
with urllib.request.urlopen('http://127.0.0.1:8765/ready',timeout=5) as response: ready=json.load(response)
result['production']={'instance':ready['instance'],'status':ready['status'],'config_sha256':sha(production/'config/service.json'),'token_sha256':sha(production/'config/api-token')}
print(json.dumps(result))
'''
args = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8',
        '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3', 'mac-mini.civet-beta.ts.net', 'python3 -']
process = subprocess.run(args, input=script, capture_output=True, text=True, timeout=55)
if process.returncode:
    print(process.stderr[-1500:])
    raise SystemExit(process.returncode)
result = json.loads(process.stdout)
stamp = result['at'].replace(':', '').replace('+', '_')
path = base / ('live-snapshot-' + stamp + '.json')
path.write_text(json.dumps(result, indent=2) + '\n')
summary = {'at': result['at'], 'processes': result['processes'], 'raw_snapshot': str(path)}
soak = result.get('data-soak-r2/reports/soak.json', {})
summary['soak'] = {k: soak[k] for k in ['status', 'cycles', 'duration_seconds', 'duration_target_seconds',
    'disconnect_recovered', 'update_pause_resume', 'error', 'failed_request', 'failed_workload', 'finished'] if k in soak}
summary['soak_runner'] = {k: v for k, v in result.get('soak-r2-result.json', {}).items() if k not in ['production_before', 'production_after']}
download = result.get('download-r2-result.json', {})
summary['download'] = {k: download[k] for k in ['status', 'finished', 'error', 'production_preserved', 'configuration_token_preserved'] if k in download}
summary['download']['attempts'] = [{k: v for k, v in row.items() if k not in ['before', 'after']} for row in download.get('attempts', [])]
summary['download']['final_files'] = len(result['final_files'])
summary['download']['final_bytes'] = sum(row['bytes'] for row in result['final_files'])
summary['download']['partials'] = result['partial_files']
retry = result.get('download-retry-result.json', {})
summary['download_retry'] = {k: retry[k] for k in ['status', 'started', 'child_pid', 'exit_code', 'error', 'finished',
    'production_preserved', 'configuration_token_preserved', 'independently_verified_bytes'] if k in retry}
summary['production'] = result['production']
print(json.dumps(summary, indent=2))
