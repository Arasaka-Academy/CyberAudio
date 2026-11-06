
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,subprocess,urllib.request
root=Path('/Users/zhubby/Library/Caches/CyberAudio/RobustnessValidation/20261106-e2f34df-target1')
folder=root/'network-refresh-20261107'
r=json.loads((folder/'result.json').read_text());h=json.loads((folder/'head-controls/result.json').read_text())
assert r['status']=='probe_failed' and r['probe']['error_code']==-1200 and not r['normal_tls_correct_range_passed']
assert r['production_preserved'] and r['assets_unchanged'] and h['status']=='diagnostic_completed' and h['production_preserved']
assert [c['exit_code'] for c in h['cases']]==[35,35,35,28]
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert sha(folder/'RangeProbe.swift')==r['source_sha256'] and sha(folder/'probe')==r['binary_sha256']
data=root/'fresh-download'
assets={str(p.relative_to(data)):{'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in data.rglob('*') if p.is_file()}
assert r['assets_before']==r['assets_after']==assets
production=Path.home()/'Library/Application Support/CyberAudio'
with urllib.request.urlopen('http://127.0.0.1:8765/ready',timeout=5) as response:state=json.load(response)
current={'instance':state['instance'],'status':state['status'],'busy':state['busy'],'config_sha256':sha(production/'config/service.json'),'token_sha256':sha(production/'config/api-token')}
assert current==r['production_before']==r['production_after']
assert all(current[k]==v==h['production_after'][k] for k,v in h['production_before'].items())
processes=subprocess.check_output(['/bin/ps','-axo','pid=,pgid=,command='],text=True).splitlines()
assert not any(str(folder/'probe') in row for row in processes)
assert not any(str(data) in row and '/CyberAudioAssets download ' in row for row in processes)
result={'status':'verified','verified_at':datetime.now(timezone.utc).isoformat(),'range_report_sha256':sha(folder/'result.json'),'head_report_sha256':sha(folder/'head-controls/result.json'),'actual_probe_source_and_binary_sha_verified':True,'retained_asset_inventory_unchanged':True,'retained_inventory_files':len(assets),'probe_and_download_processes_absent':True,'production_preserved':True,'new_full_download_started':False,'tls_validation_changed':False,'public_download_gate_accepted':False,'normal_tls_range_failed_code':-1200,'head_exit_codes':[c['exit_code'] for c in h['cases']]}
(folder/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
