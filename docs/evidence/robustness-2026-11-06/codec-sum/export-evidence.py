"""Export a precision pilot rejected by content, retaining all operator evidence."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

AUDIT = Path(__file__).resolve().parent
ROOT = AUDIT.parent / 'codec-sum-20261107'
PROJECT = AUDIT.parents[2]
DEST = PROJECT / 'docs/evidence/robustness-2026-11-06/codec-sum'
summary = json.loads((ROOT / 'generation-verification.private.json').read_text())
assert summary['status'] == 'fixed_codec_sum_pilot_generations_verified'
content = json.loads((ROOT / 'content-verification.private.json').read_text())
assert summary['necessary_generation_gate_passed']
assert content['status'] == 'fixed_codec_sum_pilot_content_verified'
assert not content['pilot_qualified_for_full_original_cohort'], 'Do not export a passing pilot as rejected'
assert summary['production_preserved'] and summary['owned_model_processes_absent']
assert not DEST.exists(), 'Preserve previous public evidence'
DEST.mkdir()


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


removed = {'production_before', 'production_after', 'startup', 'owned_identity', 'token_sha256', 'config_sha256'}


def public(value):
    if isinstance(value, dict):
        return {public(key): public(item) for key, item in value.items() if key not in removed}
    if isinstance(value, list):
        return [public(item) for item in value]
    if isinstance(value, str):
        return value.replace(str(PROJECT), '<workspace>').replace(str(Path.home()), '<home>')
    return value


records = []
files = [
    (AUDIT, 'source-provenance.json', 'official-source-provenance.json'),
    (AUDIT, 'operator-protocol.json', 'operator-protocol.json'),
    (AUDIT, 'operator-report.private.json', 'operator-report.json'),
    (AUDIT, 'mlx-operator-report.private.json', 'python-mlx-report.json'),
    (AUDIT, 'native-operator-report.private.json', 'swift-mlx-report.json'),
    (AUDIT, 'operator-native-inputs.json', 'native-inputs.json'),
    (AUDIT, 'operator-verification.private.json', 'operator-verification.json'),
    (AUDIT, 'native-operator-build/build.json', 'operator-build.json'),
    (AUDIT, 'runtime-setup.json', 'runtime-setup.json'),
    (ROOT, 'protocol.json', 'pilot-protocol.json'),
    (ROOT, 'delta.json', 'source-delta.json'),
    (ROOT, 'build/build.json', 'pilot-build.json'),
    (ROOT, 'runner.json', 'pilot-runner.json'),
    (ROOT, 'baseline/report.json', 'pilot-baseline.json'),
    (ROOT, 'candidate/report.json', 'pilot-candidate.json'),
    (ROOT, 'generation-verification.private.json', 'pilot-verification.json'),
    (ROOT, 'content-verification.private.json', 'content-verification.json'),
    (ROOT, 'roundtrip-http/runner.json', 'asr-zero-request-failure.json'),
    (ROOT, 'asr-execution-protocol.json', 'asr-execution-protocol.json'),
    (ROOT, 'roundtrip-current-http/runner.json', 'asr-runner.json'),
    (ROOT, 'roundtrip-manifest.json', 'asr-manifest.json'),
    (ROOT, 'roundtrip-transform.json', 'audio-transforms.json'),
    (ROOT, 'roundtrip-implementation.json', 'asr-initial-implementation.json'),
    (ROOT, 'source-before.json', 'source-before.json'),
    (ROOT, 'roundtrip-current-http/source-before.json', 'asr-source-before.json'),
]
for base, source, target in files:
    original = base / source
    out = DEST / target
    out.write_text(json.dumps(public(json.loads(original.read_text())), ensure_ascii=False, indent=2) + '\n')
    records.append({'source_root': base.name, 'source': source, 'source_sha256': sha(original), 'export': target,
        'export_sha256': sha(out), 'format': 'public_normalized_json'})
sources = [
    (AUDIT, name, name) for name in ['audit-operators.py', 'audit-mlx.py', 'NativeOperatorProbe.swift',
        'build-operator-probe.py', 'prepare-causal-pilot.py', 'verify-generations.py',
        'prepare-current-roundtrip.py', 'verify-content.py', 'export-evidence.py']
] + [(ROOT, name, name) for name in ['CodecSumProbe.swift', 'build.py', 'run.py',
    'prepare-roundtrip.py', 'run-roundtrip.py', 'run-current-roundtrip.py']]
for base, source, target in sources:
    original = base / source
    text = original.read_text()
    # Retain executable syntax while normalizing the sole original home literal.
    marker = "Path.home() / 'Library/Application Support/CyberAudio/models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit/model.safetensors'"
    normalized = text.replace(marker, "Path.home() / 'Library/Application Support/CyberAudio/models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit/model.safetensors'")
    assert str(Path.home()) not in normalized
    out = DEST / target
    out.write_text(normalized)
    records.append({'source_root': base.name, 'source': source, 'source_sha256': sha(original), 'export': target,
        'export_sha256': sha(out), 'format': 'exact_source_copy' if normalized == text else 'home_literal_normalized_source'})
source = ROOT / 'roundtrip-current-http/results.jsonl'
out = DEST / 'asr-results.json'
out.write_text(json.dumps(public([json.loads(line) for line in source.read_text().splitlines()]), ensure_ascii=False, indent=2) + '\n')
records.append({'source_root': ROOT.name, 'source': 'roundtrip-current-http/results.jsonl',
    'source_sha256': sha(source), 'export': out.name, 'export_sha256': sha(out), 'format': 'public_json_array_from_jsonl'})
for name in ['requirements.in', 'requirements.lock']:
    source = AUDIT / name
    out = DEST / name
    out.write_bytes(source.read_bytes())
    records.append({'source_root': AUDIT.name, 'source': name, 'source_sha256': sha(source), 'export': name,
        'export_sha256': sha(out), 'format': 'exact_source_copy'})
index = {
    'exported_at': datetime.now(timezone.utc).isoformat(), 'records': records,
    'normalization': 'JSON home/workspace paths normalized and production identity/config/token snapshots omitted. Source copies exact except the documented home model path literal in the operator analyzer and that same literal in its exporter. No audio, weights, tensor rows, environments, binaries, credentials or logs exported.',
    'source_commit': json.loads((ROOT / 'protocol.json').read_text())['source_commit'],
    'candidate_promoted': False,
}
(DEST / 'export-record.json').write_text(json.dumps(index, indent=2) + '\n')
print(json.dumps({'status': 'rejected_precision_pilot_exported', 'files': len(records) + 1}))
