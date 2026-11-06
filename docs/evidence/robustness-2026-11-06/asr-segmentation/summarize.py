"""Evaluate the predeclared pilot without changing segment or text rules."""
from pathlib import Path
import hashlib
import json
import re
import unicodedata

base = Path(__file__).resolve().parent
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
plan = read(base/'plan.json')
report = read(base/'native-http/reports/asr-followup.json')
prior = read(base.parent/'asr-reference-20261107/native-http/reports/asr-followup.json')
assert report['status']=='diagnostic_completed' and report['owned_helper_exit_code']==0
assert sha(base.parent/'asr-reference-20261107/native-http/reports/asr-followup.json')==plan['baseline_report_sha256']
observed = {r['id']:r for r in report['results']}
old = {r['id']:r for r in prior['results']}
assert len(observed)==len(report['results'])==plan['expected_requests']
assert set(observed)=={c['id'] for c in plan['cases']}


def is_han(char):
    return '\u3400'<=char<='\u4dbf' or '\u4e00'<=char<='\u9fff'


def units(text):
    result, word = [], ''
    for char in unicodedata.normalize('NFKC',text).casefold():
        if is_han(char):
            if word: result.append(word); word=''
            result.append(char)
        elif char.isalnum(): word+=char
        else:
            if word: result.append(word); word=''
    if word: result.append(word)
    return result


def distance(a,b):
    previous=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        current=[i]
        for j,y in enumerate(b,1): current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(x!=y)))
        previous=current
    return previous[-1]


def scripts(text):
    return {'han':any(is_han(c) for c in text),'latin':bool(re.search(r'[a-z]',text.casefold()))}


rows=[]
for case in plan['source_cases']:
    source=case['source']
    baseline=observed[case['baseline_request_id']]
    before=baseline['response']['text']
    assert before==old[source['id']]['response']['text'], 'Fresh original final-App text did not reproduce'
    chunks=[observed[c['request_id']] for c in case['chunks']]
    candidate=' '.join(c['response']['text'].strip() for c in chunks if c['response']['text'].strip())
    gold=units(source['reference'])
    a,b=distance(gold,units(before)),distance(gold,units(candidate))
    rows.append({'id':source['id'],'group':source['group'],'reference':source['reference'],
        'baseline_text':before,'candidate_text':candidate,'baseline_reproduced':True,'parts':len(chunks),
        'candidate_reuses_original':len(case['chunks'])==1 and case['chunks'][0]['reused_original_request'],
        'cut_seconds':[s/16000 for s in case['boundaries_samples']],
        'reference_unit_count':len(gold),'baseline_edits':a,'candidate_edits':b,'edit_delta':b-a,
        'baseline_scripts':scripts(before),'candidate_scripts':scripts(candidate),
        'baseline_processing_ms':baseline['response']['processing_ms'],
        'candidate_processing_ms':sum(c['response']['processing_ms'] for c in chunks),
        'chunk_texts':[c['response']['text'] for c in chunks]})
assert len(rows)==12
mixed=[r for r in rows if r['group']=='mixed']
checks={'all_twelve_original_texts_reproduced':True,
        'no_case_has_more_mixed_unit_edits':all(r['candidate_edits']<=r['baseline_edits'] for r in rows),
        'all_eight_mixed_candidates_contain_both_scripts':all(all(r['candidate_scripts'].values()) for r in mixed),
        'previously_missing_script_restored_with_fewer_edits':any(not all(r['baseline_scripts'].values()) and all(r['candidate_scripts'].values()) and r['candidate_edits']<r['baseline_edits'] for r in mixed)}
result={'status':'diagnostic_completed','plan_sha256':sha(base/'plan.json'),'source_sha256':sha(Path(__file__)),
        'native_report_sha256':sha(base/'native-http/reports/asr-followup.json'),
        'actual_http_requests':len(report['results']),'source_cases':12,'mixed_cases':8,'monolingual_controls':4,
        'fresh_baseline_repetitions':12,'additional_segment_requests':len(report['results'])-12,
        'pilot_gate':checks,'pilot_passed':all(checks.values()),'candidate_promoted':False,
        'formal_source_or_defaults_changed':False,'cases_with_more_edits':sum(r['edit_delta']>0 for r in rows),
        'cases_with_fewer_edits':sum(r['edit_delta']<0 for r in rows),
        'total_reference_units':sum(r['reference_unit_count'] for r in rows),
        'baseline_total_edits':sum(r['baseline_edits'] for r in rows),
        'candidate_total_edits':sum(r['candidate_edits'] for r in rows),
        'rows':rows,'limits':plan['limits']+['Mixed-unit edit distance is a diagnostic measure, not formal CER/WER or adjudicated content correctness',
        'Parenthetical names, numeral rendering and references not aligned to exact spoken words can affect this metric',
        'The four short monolingual controls reuse their identical full-file request; they do not exercise chunk-boundary behavior']}
path=base/'summary.json'; assert not path.exists()
path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ['rows','limits']},indent=2))
for row in rows: print(row['id'],'edits',row['baseline_edits'],'->',row['candidate_edits'],'scripts',row['candidate_scripts'])
