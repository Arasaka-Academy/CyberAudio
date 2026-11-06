"""Copy allowlisted text evidence; leave all tensor/audio payloads private."""
from pathlib import Path
import ast
import hashlib
import json
import shutil
import re

base = Path(__file__).resolve().parent
project = base.parents[2]
parent = project / 'docs/evidence/robustness-2026-11-06'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
complete = base.parent / 'complete-codec-20261107'
assert json.loads((complete / 'verification.json').read_text())['status'] == 'verified'
assert json.loads((base / 'verification.json').read_text())['status'] == 'verified'


def archive(source, name, files, directories=()):
    destination = parent / name
    destination.mkdir(exist_ok=False)
    mappings = []
    for src, target in files:
        p = source / src
        q = destination / target
        q.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, q)
        assert p.read_bytes() == q.read_bytes()
        mappings.append({'source': str(p.relative_to(project)), 'archived': target, 'sha256': sha(q)})
    for directory in directories:
        for p in sorted((source / directory).glob('*.json')):
            q = destination / directory / p.name
            q.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, q)
            mappings.append({'source': str(p.relative_to(project)), 'archived': str(q.relative_to(destination)), 'sha256': sha(q)})
    raw = sorted([*source.glob('native-capture/**/*.safetensors'), *source.glob('native-capture/**/*.pcm'),
                  *source.glob('reference-capture/**/*.safetensors'), *source.glob('*rope-formula.safetensors')])
    artifacts = [{'path': str(p.relative_to(project)), 'bytes': p.stat().st_size, 'sha256': sha(p)} for p in raw]
    (destination / 'private-artifacts.json').write_text(json.dumps(artifacts, indent=2) + '\n')
    (destination / 'archive-map.json').write_text(json.dumps(mappings, indent=2) + '\n')
    secret = (Path.home() / 'Library/Application Support/CyberAudio/config/api-token').read_bytes().strip()
    assert len(secret) >= 16
    for p in destination.rglob('*'):
        if p.is_file():
            data = p.read_bytes()
            assert secret not in data, f'Credential content found: {p.name}'
            assert re.search(rb'(?m)^-----BEGIN (?:OPENSSH )?PRIVATE KEY-----', data) is None
            if p.suffix == '.json':
                json.loads(data)
            if p.suffix == '.py':
                ast.parse(data.decode())
    print(name, len(mappings), 'copied text files;', len(artifacts), 'private payload references')


complete_files = [(p.name, p.name) for p in sorted(complete.glob('*.py'))]
complete_files += [(p.name, p.name) for p in sorted(complete.glob('*.json'))]
complete_files += [('CompleteCodecProbe.swift', 'CompleteCodecProbe.swift'),
                   ('native-build/build.json', 'native-build.json'),
                   ('native-capture/report.json', 'native-complete-capture.json'),
                   ('native-capture.log', 'native-capture.txt'), ('reference.log', 'reference.txt')]
archive(complete, 'complete-codec', complete_files, ['native-codes'])
layer_files = [(p.name, p.name) for p in sorted(base.glob('*.py'))]
layer_files += [(p.name, p.name) for p in sorted(base.glob('*.swift'))]
layer_files += [(p.name, p.name) for p in sorted(base.glob('*.json'))]
layer_files += [('native-build/build.json', 'native-build.json'),
                ('native-build-first-failure/build.json', 'first-native-build.json'),
                ('native-build-first-failure/module.log', 'first-native-compile.txt'),
                ('native-capture/report.json', 'native-layer-capture.json'),
                ('native-capture.log', 'native-capture.txt'), ('reference.log', 'reference.txt'),
                ('rope-scalar-build/link.txt', 'first-scalar-link.txt'),
                ('rope-scalar-build/run-followup.txt', 'scalar-run.txt')]
layer_files += [('native-frequency-injection/' + p.name, 'native-frequency-injection/' + p.name)
                for p in sorted((base / 'native-frequency-injection').iterdir())
                if p.is_file() and p.suffix in {'.json', '.py', '.log'}]
layer_files = [(p, q.removesuffix('.log') + '.txt' if q.endswith('.log') else q) for p, q in layer_files]
archive(base, 'layer-parity', layer_files)
