"""Bounded HEAD controls for existing explicit proxy and direct transport; no downloads."""
from pathlib import Path
import json
import subprocess

base = Path(__file__).resolve().parent
script = r'''
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,subprocess,time,urllib.request
root=Path('/Users/zhubby/Library/Caches/CyberAudio/RobustnessValidation/20261106-e2f34df-target1')
folder=root/'network-refresh-20261107/head-controls'
folder.mkdir(exist_ok=False)
production=Path.home()/'Library/Application Support/CyberAudio'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def snapshot():
 with urllib.request.urlopen('http://127.0.0.1:8765/ready',timeout=5) as response:state=json.load(response)
 return {'instance':state['instance'],'status':state['status'],'config_sha256':sha(production/'config/service.json'),'token_sha256':sha(production/'config/api-token')}
result={'started':datetime.now(timezone.utc).isoformat(),'production_before':snapshot(),'scope':'At most four HEAD requests, each20seconds; unchanged proxy and production; no model or full asset downloads','cases':[]}
cases=[('huggingface-proxy-default','https://huggingface.co/',['--proxy','http://127.0.0.1:7890']),('huggingface-proxy-http1','https://huggingface.co/',['--proxy','http://127.0.0.1:7890','--http1.1']),('apple-proxy-http1','https://www.apple.com/',['--proxy','http://127.0.0.1:7890','--http1.1']),('huggingface-direct-http1','https://huggingface.co/',['--noproxy','*','--http1.1'])]
for name,url,options in cases:
 args=['/usr/bin/curl','--silent','--show-error','--head','--max-time','20','--connect-timeout','8','--output','/dev/null','--dump-header',str(folder/(name+'.headers.private')),'--write-out','%{http_code} %{ssl_verify_result} %{time_total} %{http_version}']+options+[url]
 began=time.monotonic()
 p=subprocess.run(args,capture_output=True,text=True,timeout=25)
 (folder/(name+'.stderr.private')).write_text(p.stderr)
 metrics=p.stdout.strip().split()
 row={'id':name,'url':url,'exit_code':p.returncode,'elapsed_seconds':time.monotonic()-began,'metrics':p.stdout.strip(),'tls_validation':'default'}
 markers={'ssl_syscall':'SSL_ERROR_SYSCALL','connection_reset':'Connection reset','certificate_validation':'SSL certificate problem','connect_refused':'Failed to connect','timed_out':'timed out','connect_tunnel_failed':'CONNECT tunnel failed','unexpected_eof':'unexpected eof'}
 row['safe_error_categories']=[key for key,text in markers.items() if text.lower() in p.stderr.lower()]
 headers=(folder/(name+'.headers.private')).read_text()
 row['http_status_lines']=[line for line in headers.splitlines() if line.startswith('HTTP/')]
 result['cases'].append(row)
 (folder/'result.json').write_text(json.dumps(result,indent=2)+'\n')
result['production_after']=snapshot()
result['production_preserved']=result['production_before']==result['production_after']
result['finished']=datetime.now(timezone.utc).isoformat()
result['status']='diagnostic_completed' if result['production_preserved'] else 'failed'
(folder/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
'''
source = base / 'remote-head-controls.py'
assert not source.exists()
source.write_text(script)
args = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8',
        '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3', 'mac-mini.civet-beta.ts.net', 'python3 -']
process = subprocess.run(args, input=script, capture_output=True, text=True, timeout=120)
(base / 'head-ssh-stderr.private').write_text(process.stderr)
if process.returncode:
    result = {'status': 'remote_failed', 'ssh_exit_code': process.returncode}
else:
    result = json.loads(process.stdout)
    result['ssh_exit_code'] = process.returncode
(base / 'head-result.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
raise SystemExit(0 if result['status'] == 'diagnostic_completed' else 1)
