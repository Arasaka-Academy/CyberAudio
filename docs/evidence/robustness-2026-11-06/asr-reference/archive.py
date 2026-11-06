"""Copy reviewed text only. Do not copy arrays, WAV, binaries or credentials."""
from pathlib import Path
import hashlib
import json
import re

base = Path(__file__).resolve().parent
project = base.parents[2]
destination = project / 'docs/evidence/robustness-2026-11-06/asr-reference'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert json.loads((base / 'verification.json').read_text())['status'] == 'verified'
files = ['plan.json', 'runner.json', 'reference.json', 'front-runner.json', 'front-reference-runner.json',
         'front-comparison.json', 'injection-plan.json', 'injection-runner.json', 'injection.json', 'summary.json',
         'verification.json', 'run-native.py', 'transcribe-reference.py', 'run-all.py', 'ASRFrontProbe.swift',
         'run-front.py', 'compare-front-reference.py', 'run-front-reference.py', 'inject-front-reference.py',
         'run-injection.py', 'summarize.py', 'verify.py', 'archive.py', 'native-http/reports/asr-followup.json',
         'native-http/corpus.json', 'native-front/report.json', 'front-build/Objects.LinkFileList',
         'native.txt', 'reference.txt', 'front-compile.txt', 'front-link.txt', 'front-native.txt',
         'front-reference.txt', 'injection-reference.txt']
token_paths = list((project / '.context/robustness').rglob('config/api-token'))
token_paths.append(Path.home() / 'Library/Application Support/CyberAudio/config/api-token')
secrets = [p.read_bytes().strip() for p in token_paths if p.is_file()]
assert secrets and all(len(s) >= 20 for s in secrets)
for name in files:
    source, target = base / name, destination / name
    assert source.is_file() and source.stat().st_size < 1_000_000
    assert not target.exists(), str(target)
    data = source.read_bytes()
    content = data.decode('utf-8')
    assert not any(s in data for s in secrets), 'Secret detected; stop archival'
    assert not re.search(r'(?m)^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', content)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
inventory = {str(p.relative_to(destination)): sha(p) for p in sorted(destination.rglob('*')) if p.is_file()}
assert 'README.md' in inventory and len(inventory) == len(files) + 1
manifest = destination / 'archive-sha256.json'
assert not manifest.exists()
manifest.write_text(json.dumps(inventory, indent=2) + '\n')
assert inventory == {p: sha(destination / p) for p in inventory}
print(len(inventory), 'text manifest entries,', sum((destination / p).stat().st_size for p in inventory), 'bytes; no raw media/arrays/binaries/config/Token archived')
