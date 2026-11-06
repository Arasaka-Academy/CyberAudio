"""Archive whitelisted text only; all numerical arrays stay private."""
from pathlib import Path
import hashlib
import json
import re

base = Path(__file__).resolve().parent
project = base.parents[2]
destination = project / 'docs/evidence/robustness-2026-11-06/asr-mel'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
assert read(base / 'verification.json')['status'] == read(base / 'link-control.json')['status'] == 'verified'
files = ['prepare.py', 'plan.json', 'reference-constants.py', 'MelDSPProbe.swift', 'run-native.py',
         'runner.json', 'analyze.py', 'run-analysis.py', 'analysis-runner.json', 'comparison.json',
         'verify.py', 'verification.json', 'verify-link.py', 'link-control.json', 'archive.py',
         'link-check-first-failure/verify-link.py', 'link-check-first-failure/failure.json',
         'native/report.json', 'prepare.txt', 'reference-constants.txt', 'compile.txt', 'link.txt',
         'native.txt', 'analysis.txt']
tokens = list((project / '.context/robustness').rglob('config/api-token'))
tokens.append(Path.home() / 'Library/Application Support/CyberAudio/config/api-token')
secrets = [p.read_bytes().strip() for p in tokens if p.is_file()]
assert secrets and all(len(s) >= 20 for s in secrets)
for name in files:
    source, target = base / name, destination / name
    assert source.is_file() and source.stat().st_size < 1_000_000
    assert not target.exists(), str(target)
    data = source.read_bytes(); value = data.decode('utf-8')
    assert not any(secret in data for secret in secrets), 'Secret detected; stop archival'
    assert not re.search(r'(?m)^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', value)
    target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
inventory = {str(p.relative_to(destination)): sha(p) for p in sorted(destination.rglob('*')) if p.is_file()}
assert len(inventory) == len(files) + 1 and 'README.md' in inventory
manifest = destination / 'archive-sha256.json'
assert not manifest.exists()
manifest.write_text(json.dumps(inventory, indent=2) + '\n')
assert inventory == {p: sha(destination / p) for p in inventory}
print(len(inventory), 'text manifest entries,', sum((destination / p).stat().st_size for p in inventory), 'bytes; arrays and credentials remain private')
