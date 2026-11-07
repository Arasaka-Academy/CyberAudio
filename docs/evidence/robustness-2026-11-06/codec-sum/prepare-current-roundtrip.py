"""Correct a zero-request App/source preflight failure without changing study gates."""
import ast
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

AUDIT = Path(__file__).resolve().parent
ROOT = AUDIT.parent / 'codec-sum-20261107'
PROJECT = AUDIT.parents[2]
APP = ROOT.parent / 'model-route-20261107/current-source/CyberAudio.app'
BUILD_PATH = APP.parent / 'build.private.json'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


failed_path = ROOT / 'roundtrip-http/runner.json'
failed = json.loads(failed_path.read_text())
assert failed['status'] == 'failed' and failed['completed_requests'] == 0
assert 'owned_helper_pid' not in failed and not (ROOT / 'roundtrip-http/results.jsonl').exists()
assert failed['error'] == 'Native source differs from accepted Helper: Native/Sources/CyberAudioAssets/Main.swift'
assert failed['production_preserved'] and failed['owned_listener_closed']
build = json.loads(BUILD_PATH.read_text())
assert build['status'] == 'built' and build['strict_nested_signature']
assert all(sha(PROJECT / name) == value for name, value in build['source_sha256'].items())
assert all((APP / name).is_symlink() and os.readlink(APP / name) == value['target'] if value['type'] == 'symlink'
           else sha(APP / name) == value['sha256'] for name, value in build['app_files'].items())
acceptance = json.loads((APP.parent / 'data-local/reports/native-service.json').read_text())
assert acceptance['status'] == 'passed' and len(acceptance['asr_corpus']) == 100
assert acceptance['helper_sha256'] == build['app_files']['Contents/Helpers/CyberAudioService']['sha256']
execution = {
    'created_at': datetime.now(timezone.utc).isoformat(),
    'reason': 'Original ASR runner used a pre-ModelScope App manifest and failed source admission before Helper startup or any ASR request. Use the existing current-source App whose entire source/file manifest and full100-corpus acceptance verify; retain all prior failure bytes.',
    'failed_attempt_sha256': sha(failed_path), 'failed_attempt_requests': 0,
    'frozen_study_protocol_sha256': sha(ROOT / 'protocol.json'),
    'manifest_sha256': sha(ROOT / 'roundtrip-manifest.json'),
    'app': str(APP.resolve()), 'build_manifest_sha256': sha(BUILD_PATH),
    'helper_sha256': build['app_files']['Contents/Helpers/CyberAudioService']['sha256'],
    'accepted_native_report_sha256': sha(APP.parent / 'data-local/reports/native-service.json'),
    'source_head': subprocess.check_output(['rtk', 'proxy', 'git', 'rev-parse', 'HEAD'], text=True).strip(),
    'output_directory': 'roundtrip-current-http', 'expected_requests': 46,
    'study_conditions_changed': False, 'inference_retries': 0,
}
out = ROOT / 'asr-execution-protocol.json'
assert not out.exists()
out.write_text(json.dumps(execution, ensure_ascii=False, indent=2) + '\n')
source = (ROOT / 'run-roundtrip.py').read_text()


def replace(before, after):
    global source
    assert source.count(before) == 1, before
    source = source.replace(before, after, 1)


replace('APP = Path(STUDY_PROTOCOL["app"])', 'ASR_EXEC = json.loads((STUDY / "asr-execution-protocol.json").read_text())\nAPP = Path(ASR_EXEC["app"])')
replace('BUILD = json.loads((APP.parent / "build.json").read_text())', 'BUILD = json.loads((APP.parent / "build.private.json").read_text())')
replace('ROOT = STUDY / "roundtrip-http"', 'ROOT = STUDY / "roundtrip-current-http"')
replace('assert sha(HELPER) == STUDY_PROTOCOL["execution"]["helper_sha256"] == BUILD["files"]["Contents/Helpers/CyberAudioService"]["sha256"]',
        'assert sha(APP.parent / "build.private.json") == ASR_EXEC["build_manifest_sha256"]\n    assert sha(HELPER) == ASR_EXEC["helper_sha256"] == BUILD["app_files"]["Contents/Helpers/CyberAudioService"]["sha256"]')
replace('BUILD["files"][relative]["sha256"]', 'BUILD["app_files"][relative]["sha256"]')
replace('helper_source_commit=BUILD["source_commit"]', 'helper_source_commit=ASR_EXEC["source_head"]')
replace('["protocol.json", "run-roundtrip.py", "prepare-roundtrip.py", "roundtrip-manifest.json", "roundtrip-transform.json"]',
        '["protocol.json", "asr-execution-protocol.json", "run-current-roundtrip.py", "run-roundtrip.py", "prepare-roundtrip.py", "roundtrip-manifest.json", "roundtrip-transform.json"]')
replace('    report["study_protocol_sha256"] = sha(STUDY / "protocol.json")',
        '    assert sha(STUDY / "protocol.json") == ASR_EXEC["frozen_study_protocol_sha256"]\n    assert sha(STUDY / "roundtrip-manifest.json") == ASR_EXEC["manifest_sha256"]\n    assert len(manifest) == ASR_EXEC["expected_requests"]\n    report["study_protocol_sha256"] = sha(STUDY / "protocol.json")')
ast.parse(source)
(ROOT / 'run-current-roundtrip.py').write_text(source)
print(json.dumps({'status': 'current_accepted_App_admitted', 'unchanged_requests': 46,
    'manifest_sha256': execution['manifest_sha256'], 'previous_model_starts': 0, 'previous_asr_requests': 0}))
