"""Whitelist reviewed text evidence; keep media/config/Token private."""
from pathlib import Path
import hashlib
import json
import re

base=Path(__file__).resolve().parent
project=base.parents[2]
destination=project/'docs/evidence/robustness-2026-11-06/asr-segmentation'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert json.loads((base/'verification.json').read_text())['status']=='verified'
files=['prepare.py','plan.json','prepare-runner.py','collector-provenance.json','run.py','run-native.py',
       'runner.json','summarize.py','summary.json','verify.py','verification.json','archive.py',
       'prepare.txt','prepare-runner.txt','native-http.txt','native-http/corpus.json','native-http/reports/asr-followup.json']
tokens=list((project/'.context/robustness').rglob('config/api-token'))
tokens.append(Path.home()/'Library/Application Support/CyberAudio/config/api-token')
secrets=[p.read_bytes().strip() for p in tokens if p.is_file()]
assert secrets and all(len(v)>=20 for v in secrets)
for name in files:
    source,target=base/name,destination/name
    assert source.is_file() and source.stat().st_size<1_000_000 and not target.exists()
    data=source.read_bytes();value=data.decode('utf-8')
    assert not any(s in data for s in secrets),'Secret detected; stop archival'
    assert not re.search(r'(?m)^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',value)
    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
manifest={str(p.relative_to(destination)):sha(p) for p in sorted(destination.rglob('*')) if p.is_file()}
assert len(manifest)==len(files)+1 and 'README.md' in manifest
path=destination/'archive-sha256.json';assert not path.exists()
path.write_text(json.dumps(manifest,indent=2)+'\n')
assert manifest=={p:sha(destination/p) for p in manifest}
print(len(manifest),'text payloads;',sum((destination/p).stat().st_size for p in manifest),'bytes; no WAV/config/Token')
