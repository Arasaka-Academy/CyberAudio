"""Whitelist only reviewed text evidence; never archive audio, arrays or binaries."""
from pathlib import Path
import hashlib
import json
import re

base = Path(__file__).resolve().parent
project = base.parents[2]
destination = project / 'docs/evidence/robustness-2026-11-06'
read = lambda p: json.loads(Path(p).read_text())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert read(base / 'verification.json')['status'] == 'verified'
network = base.parent / 'download-transport-20261107'
verification = read(network / 'verification.json')
assert verification['status'] == 'verified'
for source, target, remove, expected in [
    ('probe-result.json', 'range-result.json', ['remote_source_sha256', 'ssh_exit_code'], 'range_report_sha256'),
    ('head-result.json', 'head-controls-result.json', ['ssh_exit_code'], 'head_report_sha256')]:
    value = read(network / source)
    for key in remove:
        value.pop(key)
    path = network / target
    assert not path.exists()
    path.write_text(json.dumps(value, indent=2) + '\n')
    assert sha(path) == verification[expected]
power_files = ['build-control.py', 'plan.json', 'runner.json', 'generation-plan.json', 'generation-runner.json',
               'scalar-comparison.json', 'summary.json', 'verification.json', 'dispatch-provenance.json',
               'reference-disassembly-receipt.json', 'reference-power-dispatch.txt', 'power-dispatch.patch',
               'run-generations.py', 'run-roundtrip.py', 'compare-scalar.py', 'summarize.py', 'verify.py', 'archive.py',
               'generation-build/build.json', 'generation-build-first-failure/failure.json',
               'generation-build-first-failure/run-generations.py',
               'generation-original-jit/report.json', 'generation-precompiled-power/report.json',
               'power-roundtrip/reports/asr-followup.json', 'power-roundtrip/corpus.json',
               'native-build/control.LinkFileList', 'native-build/precompiled-power.LinkFileList',
               'generation-build/original-jit.LinkFileList', 'generation-build/precompiled-power.LinkFileList']
power_files += ['native-build/' + label + '.txt' for label in ['compile', 'original-jit-link', 'original-jit-scalar', 'precompiled-power-link', 'precompiled-power-scalar']]
power_files += ['generation-build/' + label + '.txt' for label in ['original-jit-link', 'precompiled-power-link', 'original-jit-generation', 'precompiled-power-generation']]
mapping = {destination / 'power-dispatch' / name: base / name for name in power_files}
mapping[destination / 'power-dispatch/RopeScalarProbe.swift'] = base.parent / 'layer-parity-20261107/RopeScalarProbe.swift'
mapping[destination / 'power-dispatch/OrderCandidateProbe.swift'] = base.parent / 'sampling-parity-20261107/OrderCandidateProbe.swift'
network_files = ['probe-remote.py', 'diagnose-remote.py', 'verify-remote.py', 'remote-range-probe.py',
                 'remote-head-controls.py', 'remote-verify.py', 'RangeProbe.swift', 'probe-result.json',
                 'range-result.json', 'head-result.json', 'head-controls-result.json', 'verification.json']
mapping.update({destination / 'transport-refresh' / name: network / name for name in network_files})
snapshot = read(project / '.context/robustness/m4-acceptance-20261106/live-snapshot-2026-11-06T184417.599378_0000.json')
safe_snapshot = {k: snapshot[k] for k in ['at', 'processes', 'partial_files', 'final_files', 'production']}
path = network / 'm4-before.json'
assert not path.exists()
path.write_text(json.dumps(safe_snapshot, indent=2) + '\n')
mapping[destination / 'transport-refresh/m4-before.json'] = path
secrets = [(Path.home() / 'Library/Application Support/CyberAudio/config/api-token').read_bytes().strip(),
           (base / 'power-roundtrip/config/api-token').read_bytes().strip()]
assert all(len(v) >= 20 for v in secrets)
for target, source in mapping.items():
    assert not target.exists(), str(target)
    assert source.is_file() and source.stat().st_size < 2_000_000
    data = source.read_bytes()
    text = data.decode('utf-8')
    assert not any(secret in data for secret in secrets), 'Secret detected; stop archival'
    assert not re.search(r'(?m)^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', text), 'Private key detected'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
for folder in ['power-dispatch', 'transport-refresh']:
    root = destination / folder
    inventory = {str(path.relative_to(root)): sha(path) for path in sorted(root.rglob('*')) if path.is_file()}
    assert 'README.md' in inventory
    manifest = root / 'archive-sha256.json'
    assert not manifest.exists()
    manifest.write_text(json.dumps(inventory, indent=2) + '\n')
    assert inventory == {name: sha(root / name) for name in inventory}
    print(folder, len(inventory), 'manifest entries', sum((root / p).stat().st_size for p in inventory), 'bytes')
print('Archived whitelisted text only; raw audio/arrays/binaries/full disassembly remain private')
