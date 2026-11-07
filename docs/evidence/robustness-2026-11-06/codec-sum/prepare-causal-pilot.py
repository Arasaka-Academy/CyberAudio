"""Freeze one source-grounded precision intervention; no inference here."""
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
ROOT.mkdir(mode=0o700, exist_ok=False)
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
operator = json.loads((AUDIT / 'operator-report.private.json').read_text())
native = json.loads((AUDIT / 'native-operator-report.private.json').read_text())
assert operator['status'] == 'verified' and operator['all_native_reconstructions_exact']
assert operator['all_independent_numpy_comparisons_exact'] and operator['changed_feedback_frames'] == 361
assert all(row['fp32_sum_exact_official_frames'] == 367 for row in native['results'])
prior = ROOT.parent / 'repeated-code-20261107'
old = json.loads((prior / 'protocol.json').read_text())
reference_path = Path(old['baseline_reference'])
references = json.loads(reference_path.read_text())['results']
for row in references:
    row['pcm_file'] = str(reference_path.parent / row['pcm_file'])
    assert sha(row['pcm_file']) == row['pcm_sha256']
cases = list(old['cases'])
matrix_path = prior / 'matrix-plan.json'
matrix = json.loads(matrix_path.read_text())
guard = next(x for x in matrix['baselines'] if x['id'] == 'zh-stability-ono_anna-s117')
assert sha(guard['pcm_source']) == guard['pcm_sha256'] and sha(guard['tokens_source']) == guard['tokens_sha256']
case = {k: v for k, v in guard['case'].items() if k != 'text_id'}
case['id'] = 'known-eos-long-s117'
cases.append(case)
references.append({'id': case['id'], 'case': case, 'tokens': json.loads(Path(guard['tokens_source']).read_text()),
    'pcm_file': guard['pcm_source'], 'pcm_sha256': guard['pcm_sha256'],
    'token_count': guard['token_count'], 'audio_seconds': guard['audio_seconds'], 'eos_seen': guard['eos_seen']})
assert references[-1]['token_count'] == len(references[-1]['tokens']) == 1085
(ROOT / 'baseline-reference.private.json').write_text(json.dumps({'results': references}, ensure_ascii=False, indent=2) + '\n')
before = '''            var codecEmbed = talker.getInputEmbeddings()(nextToken)
            for (i, code) in codeTokens.dropFirst().enumerated() {
                codecEmbed = codecEmbed + talker.codePredictor.codecEmbedding[i](code)
            }
'''
after = '''            // Private operator-parity diagnostic: official torch BF16 reduction
            // accumulates in FP32, then rounds once before the text addition.
            let codecEmbeds = [talker.getInputEmbeddings()(nextToken)]
                + codeTokens.dropFirst().enumerated().map { i, code in
                    talker.codePredictor.codecEmbedding[i](code)
                }
            let codecEmbed = sum(
                concatenated(codecEmbeds, axis: 1).asType(.float32),
                axis: 1, keepDims: true
            ).asType(codecEmbeds[0].dtype)
'''
source = Path(old['sdk_source']).read_text()
assert source.count(before) == 1
(ROOT / 'delta.json').write_text(json.dumps({'before': before, 'after': after}, indent=2) + '\n')
caller = (prior / 'RepeatedCodeProbe.swift').read_text().replace('RepeatedCodeProbe', 'CodecSumProbe').replace('Order candidate failed:', 'Codec-sum diagnostic failed:')
(ROOT / 'CodecSumProbe.swift').write_text(caller)
build = (prior / 'build.py').read_text().replace('RepeatedCodeProbe', 'CodecSumProbe')
start = build.index('assert text.count("        // Greedy if temperature 0\\n") == 1')
end = build.index('original_map =', start)
build = build[:start] + '''delta = json.loads((ROOT / "delta.json").read_text())
assert text.count(delta["before"]) == 1
private.write_text(text.replace(delta["before"], delta["after"], 1))
assert private.read_text().replace(delta["after"], delta["before"], 1) == text
''' + build[end:]
needle = 'arguments[arguments.index("-emit-objc-header-path") + 1] = str(objects / "include/MLXAudioTTS-Swift.h")'
assert build.count(needle) == 1
build = build.replace(needle, needle + '\narguments[arguments.index("-module-cache-path") + 1] = str(BUILD / "ModuleCache")')
needle = 'compile_command[-1] = str(ROOT / "CodecSumProbe.swift")'
assert build.count(needle) == 1
build = build.replace(needle, needle + '\ncompile_command[compile_command.index("-module-cache-path") + 1] = str(BUILD / "ModuleCache")')
(ROOT / 'build.py').write_text(build)
run = (prior / 'run.py').read_text()
run = run.replace('repeated-code', 'codec-sum').replace('Repeated-code', 'Codec-sum')
run = run.replace('== 6', '== 7').replace('"baseline_exact_matches"] = 6', '"baseline_exact_matches"] = 7')
run = run.replace('"generations": 6', '"generations": 7').replace('all six retained paths', 'all seven retained paths')
start = run.index('    controls_exact =')
end = run.index('except BaseException as error:', start)
run = run[:start] + '''    actual_eos = all(row["eos_seen"] for row in candidate)
    within_limit = all(row["audio_seconds"] <= 15 for row in candidate)
    failures = [row for row in candidate if row["id"] in ["known-failure", "known-failure-repeat"]]
    repeats_exact = failures[0]["tokens"] == failures[1]["tokens"] and failures[0]["pcm_sha256"] == failures[1]["pcm_sha256"]
    first_sample_exact = all(left["tokens"][0] == right["tokens"][0] for left, right in zip(original, candidate))
    record.update(status="generations_complete_not_content_accepted", all_seven_actual_eos=actual_eos,
                  all_seven_within15_seconds=within_limit, candidate_repeats_exact=repeats_exact,
                  all_first_samples_unchanged=first_sample_exact,
                  necessary_generation_gate_passed=bool(actual_eos and within_limit and repeats_exact and first_sample_exact))
''' + run[end:]
(ROOT / 'run.py').write_text(run)
for name in ['build.py', 'run.py']:
    ast.parse((ROOT / name).read_text())
plan = {k: old[k] for k in ['temperature', 'subtalker_temperature', 'top_k', 'top_p', 'repetition_penalty', 'max_tokens',
    'total_budget_seconds', 'individual_budget_seconds', 'model_directory', 'metallib', 'sdk_source', 'sdk_source_sha256', 'app', 'execution', 'legacy_baseline']}
plan['execution'] = dict(plan['execution'], port=18906)
plan.update({
    'purpose': 'Single-variable private official BF16 codec reduction parity pilot',
    'created_at': datetime.now(timezone.utc).isoformat(),
    'source_commit': subprocess.check_output(['rtk', 'proxy', 'git', 'rev-parse', 'HEAD'], text=True).strip(),
    'hypothesis': 'Sequential BF16 codec embedding additions introduce repeated rounding absent from official tensor reduction. Exact CPU/Python-MLX/Swift-MLX evidence supports testing FP32 accumulation then one BF16 conversion, without attributing quality before generation/readback.',
    'operator_report_sha256': sha(AUDIT / 'operator-report.private.json'),
    'native_operator_report_sha256': sha(AUDIT / 'native-operator-report.private.json'),
    'operator_protocol_sha256': sha(AUDIT / 'operator-protocol.json'),
    'upstream_modeling_sha256': sha(AUDIT / 'upstream/qwen_tts/core/models/modeling_qwen3_tts.py'),
    'cases': cases, 'original_pilot_cases': 6, 'preexisting_long_eos_guard_seed': 117,
    'selection': 'The unchanged original six pilot cases, followed by the already known original-cohort seed117 long-EOS failure. No seed search, reselection or output-dependent expansion.',
    'baseline_reference': str(ROOT / 'baseline-reference.private.json'),
    'baseline_reference_sha256': sha(ROOT / 'baseline-reference.private.json'),
    'candidate': 'Only replace the16 codec embedding BF16 left-fold with concatenation, FP32 tensor sum, and one BF16 conversion in generateVoiceDesign. Keep all other generation source bytes, sampling/filter order, temperatures, RNG calls, layout, EOS, decoder and1200-token budget.',
    'qualification': [
        'Reproduce all seven complete original baselines, including both96s/no-EOS repeats and seed11786.72s/EOS, with exact retained token and PCM bytes before starting any candidate generation.',
        'Generate all seven fixed candidates exactly once, even after a failed EOS/length result. No truncation, automatic retry, seed search, threshold change or parameter adjustment.',
        'Require actual EOS and <=15s complete audio in every candidate, exact failure repeats, and identical first main samples. Failed necessary generation conditions reject the pilot before ASR expansion.',
        'Only after necessary generation gates pass, transcribe every one of the14 complete baseline/candidate outputs in deterministic<=24s chunks, with both auto and original producer language. Preserve every piece; no omission/retry. Use the unchanged author mixed-token scorer and independent edit-distance crosscheck.',
        'Require no paired candidate edit increase in either ASR route for any case, and exact known-failure sentence readback in both routes. Human intelligibility remains a separate gate.',
        'Only a passed pilot can qualify the unchanged full4-text x9-voice x4-seed144-case cohort. Neither pilot nor matrix alone closes the original strict7200s M4 or physical/listening/distribution gates.',
        'Preserve production instance/config/token, models, formal SDK/shared objects, every prior artifact/failure, and App/DMG. No production promotion from operator parity.'
    ],
    'files_sha256': {name: sha(ROOT / name) for name in ['CodecSumProbe.swift', 'delta.json', 'build.py', 'run.py', 'baseline-reference.private.json']},
    'formal_source_changed': False, 'candidate_promoted': False,
})
(ROOT / 'protocol.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'status': 'causal_precision_pilot_frozen', 'cases_per_arm': len(cases), 'protocol_sha256': sha(ROOT / 'protocol.json'), 'new_model_generations': 0}))
