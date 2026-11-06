"""One bounded read-only M4 range refresh; never restart full downloads/models."""
from pathlib import Path
import hashlib
import json
import subprocess

base=Path(__file__).resolve().parent
script=r'''
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,subprocess,urllib.request
root=Path('/Users/zhubby/Library/Caches/CyberAudio/RobustnessValidation/20261106-e2f34df-target1')
oldroot=root/'network-refresh-20261107'
old=json.loads((oldroot/'result.json').read_text())
probe=oldroot/'probe'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert sha(probe)==old['binary_sha256']
data=root/'fresh-download'
def inventory():
 return {str(p.relative_to(data)):{'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in data.rglob('*') if p.is_file()}
def owned():
 rows=subprocess.check_output(['/bin/ps','-axo','pid=,pgid=,command='],text=True).splitlines()
 return [row for row in rows if str(root) in row]
production=Path.home()/'Library/Application Support/CyberAudio'
def snapshot():
 with urllib.request.urlopen('http://127.0.0.1:8765/ready',timeout=5) as f:state=json.load(f)
 return {'instance':state['instance'],'status':state['status'],'busy':state['busy'],'config_sha256':sha(production/'config/service.json'),'token_sha256':sha(production/'config/api-token')}
before=snapshot();assets=inventory()
assert before==old['production_after'] and before['status']=='ready' and not before['busy']
assert assets==old['assets_after'] and not owned()
folder=root/'acceptance-audit-20261107';folder.mkdir(exist_ok=False)
result={'status':'running','started':datetime.now(timezone.utc).isoformat(),'scope':'One repeated existing normal-TLS 1MiB probe only after confirming all owned workloads absent','production_before':before,'assets_before':assets,'old_result_sha256':sha(oldroot/'result.json'),'probe_binary_sha256':sha(probe),'new_full_download_started':False,'model_loaded':False,'tls_configuration_changed':False}
def save():(folder/'range-refresh.json').write_text(json.dumps(result,indent=2)+'\n')
save()
try:
 p=subprocess.run([str(probe)],capture_output=True,text=True,timeout=60)
 (folder/'probe.stdout.txt').write_text(p.stdout)
 (folder/'probe.stderr.private').write_text(p.stderr)
 result['probe_exit_code']=p.returncode
 assert p.returncode==0
 observed=json.loads(p.stdout);result['probe']=observed
 # Use the pinned lock size for the complete content-range check.
 lock=json.loads((root/'Models.lock.json').read_text())
 asset=next(r for r in lock['files'] if r['path']=='tts/model.safetensors')
 result['normal_tls_correct_range_passed']=(observed.get('status')==206 and observed.get('bytes')==1048576 and observed.get('sha256')==old['expected_range_sha256'] and observed.get('content_range')=='bytes 117440512-118489087/'+str(asset['size']))
 result['status']='probe_passed' if result['normal_tls_correct_range_passed'] else 'probe_failed'
except Exception as error:
 result.update(status='failed',error_type=type(error).__name__,error=str(error))
finally:
 result['production_after']=snapshot();result['assets_after']=inventory()
 result['production_preserved']=before==result['production_after']
 result['assets_preserved']=assets==result['assets_after']
 result['owned_processes_absent']=not owned()
 result['finished']=datetime.now(timezone.utc).isoformat()
 if not all(result[k] for k in ['production_preserved','assets_preserved','owned_processes_absent']):result['status']='failed'
 save()
print(json.dumps(result))
'''
path=base/'remote-range-refresh.py';assert not path.exists();path.write_text(script)
args=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10',
      '-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2','mac-mini.civet-beta.ts.net','rtk proxy python3 -']
process=subprocess.run(args,input=script,capture_output=True,text=True,timeout=85)
(base/'refresh.stderr.private').write_text(process.stderr)
(base/'refresh.stdout.private').write_text(process.stdout)
if process.returncode==0:
 result=json.loads(process.stdout);result['ssh_exit_code']=0
else:
 result={'status':'remote_failed','ssh_exit_code':process.returncode}
result['remote_source_sha256']=hashlib.sha256(script.encode()).hexdigest()
result['launcher_source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
(base/'m4-refresh.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if not k.startswith('assets_')},indent=2))
raise SystemExit(0 if result['status'] in ['probe_passed','probe_failed'] else 1)
