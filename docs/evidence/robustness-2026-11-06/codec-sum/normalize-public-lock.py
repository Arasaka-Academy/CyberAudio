"""Normalize only uv's generated command comment; retain every dependency byte."""
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

AUDIT = Path(__file__).resolve().parent
PROJECT = AUDIT.parents[2]
DEST = PROJECT / 'docs/evidence/robustness-2026-11-06/codec-sum'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
index_path = DEST / 'export-record.json'
archive = AUDIT / 'export-before-comment-normalization.private.json'
assert not archive.exists()
shutil.copyfile(index_path, archive)
index = json.loads(index_path.read_text())
original = (AUDIT / 'requirements.lock').read_text()
published = (DEST / 'requirements.lock').read_text()
assert published == original
lines = []
for line in published.splitlines(keepends=True):
    if str(Path.home()) in line:
        assert line.startswith('#'), 'Never normalize dependency content'
        line = line.replace(str(PROJECT), '<workspace>').replace(str(Path.home()), '<home>')
    lines.append(line)
normalized = ''.join(lines)
dependencies = lambda text: ''.join(line for line in text.splitlines(keepends=True) if not line.startswith('#'))
assert dependencies(normalized) == dependencies(original)
(DEST / 'requirements.lock').write_text(normalized)
row = next(row for row in index['records'] if row['export'] == 'requirements.lock')
row.update(format='generated_comment_paths_normalized_lock', export_sha256=sha(DEST / 'requirements.lock'),
    dependency_content_sha256=hashlib.sha256(dependencies(original).encode()).hexdigest())
script = Path(__file__).resolve()
shutil.copyfile(script, DEST / script.name)
index['records'].append({'source_root': AUDIT.name, 'source': script.name, 'source_sha256': sha(script),
    'export': script.name, 'export_sha256': sha(DEST / script.name), 'format': 'exact_source_copy'})
index['normalization'] += ' The generated uv command comment in requirements.lock has home/workspace paths normalized; dependency lines and hashes remain byte-identical. The first export manifest is retained privately.'
index['comment_normalization_at'] = datetime.now(timezone.utc).isoformat()
index_path.write_text(json.dumps(index, indent=2) + '\n')
print(json.dumps({'status': 'generated_comment_normalized', 'dependency_content_unchanged': True}))
