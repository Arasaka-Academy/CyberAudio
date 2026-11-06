"""Bounded normal-TLS URLSession probe on the existing M4 route; no asset changes."""
from pathlib import Path
import hashlib
import json
import subprocess

base = Path(__file__).resolve().parent
script = r'''
from datetime import datetime, timezone
from pathlib import Path
import hashlib,json,subprocess,urllib.request
root=Path('/Users/zhubby/Library/Caches/CyberAudio/RobustnessValidation/20261106-e2f34df-target1')
folder=root/'network-refresh-20261107'
assert not folder.exists()
data=root/'fresh-download'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
production=Path.home()/'Library/Application Support/CyberAudio'
def snapshot():
 with urllib.request.urlopen('http://127.0.0.1:8765/ready',timeout=5) as response:state=json.load(response)
 return {'instance':state['instance'],'status':state['status'],'busy':state['busy'],'config_sha256':sha(production/'config/service.json'),'token_sha256':sha(production/'config/api-token')}
def assets():
 return {str(p.relative_to(data)):{'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in data.rglob('*') if p.is_file()}
processes=subprocess.check_output(['/bin/ps','-axo','pid=,pgid=,command='],text=True).splitlines()
assert not any(str(data) in s and '/CyberAudioAssets download ' in s for s in processes)
original=root/'NetworkRangeProbe.swift'
text=original.read_text()
assert text.count('117440512-125829119')==2
folder.mkdir()
source=folder/'RangeProbe.swift'
source.write_text(text.replace('117440512-125829119','117440512-118489087'))
before=snapshot()
asset_before=assets()
assert before['status']=='ready' and not before['busy']
result={'status':'running','started':datetime.now(timezone.utc).isoformat(),'scope':'One normal-TLS 1MiB range through the unchanged existing explicit proxy; no full download or model inference','production_before':before,'assets_before':asset_before,'original_source_sha256':sha(original),'source_sha256':sha(source),'steps':[]}
def save():
 (folder/'result.json').write_text(json.dumps(result,indent=2)+'\n')
save()
try:
 command=['/usr/bin/swiftc','-parse-as-library','-target','arm64-apple-macos26.0',str(source),'-o',str(folder/'probe')]
 p=subprocess.run(command,capture_output=True,text=True,timeout=120)
 (folder/'compile.txt').write_text(p.stdout+p.stderr)
 result['steps'].append({'name':'compile','args':command,'exit_code':p.returncode})
 assert p.returncode==0,'Probe compile failed'
 result['binary_sha256']=sha(folder/'probe')
 p=subprocess.run([str(folder/'probe')],capture_output=True,text=True,timeout=60)
 (folder/'run.txt').write_text(p.stdout)
 (folder/'stderr.private').write_text(p.stderr)
 result['steps'].append({'name':'probe','exit_code':p.returncode})
 assert p.returncode==0,'Probe execution failed'
 result['probe']=json.loads(p.stdout)
 probe=result['probe']
 rows=json.loads((root/'Models.lock.json').read_text())['files']
 row=next(r for r in rows if r['path']=='tts/model.safetensors')
 config=json.loads((production/'config/service.json').read_text())
 model=Path(config['tts_dir'])/'model.safetensors'
 with model.open('rb') as reader:
  reader.seek(117440512);expected=reader.read(1048576)
 assert len(expected)==1048576
 result['expected_range_sha256']=hashlib.sha256(expected).hexdigest()
 result['normal_tls_correct_range_passed']=(probe.get('status')==206 and probe.get('bytes')==1048576 and probe.get('content_range')=='bytes 117440512-118489087/'+str(row['size']) and probe.get('sha256')==result['expected_range_sha256'])
 result['status']='probe_passed' if result['normal_tls_correct_range_passed'] else 'probe_failed'
except Exception as error:
 result.update(status='failed',error_type=type(error).__name__,error=str(error))
finally:
 result['production_after']=snapshot()
 result['assets_after']=assets()
 result['production_preserved']=result['production_before']==result['production_after']
 result['assets_unchanged']=asset_before==result['assets_after']
 result['finished']=datetime.now(timezone.utc).isoformat()
 if not result['production_preserved'] or not result['assets_unchanged']:result['status']='failed'
 save()
print(json.dumps(result))
'''
source = base / 'remote-range-probe.py'
assert not source.exists()
source.write_text(script)
args = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8',
        '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3', 'mac-mini.civet-beta.ts.net', 'python3 -']
process = subprocess.run(args, input=script, capture_output=True, text=True, timeout=220)
(base / 'probe-ssh-stderr.private').write_text(process.stderr)
if process.returncode:
    result = {'status': 'remote_failed', 'ssh_exit_code': process.returncode, 'remote_source_sha256': hashlib.sha256(script.encode()).hexdigest()}
else:
    result = json.loads(process.stdout)
    result['remote_source_sha256'] = hashlib.sha256(script.encode()).hexdigest()
    result['ssh_exit_code'] = process.returncode
(base / 'probe-result.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({k: v for k, v in result.items() if not k.startswith('assets_')}, indent=2))
raise SystemExit(0 if result['status'] in ['probe_passed', 'probe_failed'] else 1)
