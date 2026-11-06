"""Create a scoped copy of the retained final-App HTTP collector."""
from pathlib import Path
import hashlib
import json

base = Path(__file__).resolve().parent
source = base.parent / 'asr-reference-20261107/run-native.py'
original = source.read_text()
content = original
changes = [
    ('"""Transcribe every predeclared matrix output after the TTS process terminates."""',
     '"""Transcribe all fixed one-shot and energy-segment requests serially."""'),
    ('power-kernel-20261107/verification.json', 'asr-mel-20261107/verification.json'),
    ('previous["all_owned_processes_absent"]', 'previous["owned_processes_absent"]'),
    ('port = 18892', 'port = 18894'),
    ('Current final-App auto-ASR on all twelve fixed prior bilingual/control inputs; no synthetic regeneration or parameter changes',
     'Current final-App paired original/energy-segment pilot on all twelve retained inputs; no SDK/model/default change'),
    ('report["expected_source_outputs"] = len(plan["cases"])', 'report["expected_source_outputs"] = len(plan["source_cases"])')]
for old, new in changes:
    assert content.count(old) == 1
    content = content.replace(old, new)
path = base / 'run-native.py'
assert not path.exists()
path.write_text(content)
(base / 'collector-provenance.json').write_text(json.dumps({'source':str(source),
    'original_sha256':hashlib.sha256(original.encode()).hexdigest(), 'generated_sha256':hashlib.sha256(content.encode()).hexdigest(),
    'exact_replacements':changes},indent=2)+'\n')
print('Prepared one isolated HTTP collector, new port18894; prior source retained')
