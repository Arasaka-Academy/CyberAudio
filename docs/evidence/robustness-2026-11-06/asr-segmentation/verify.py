"""Independent PCM, segmentation, metric and preservation checks; no inference."""
from datetime import datetime, timezone
from pathlib import Path
import ast
import hashlib
import itertools
import json
import re
import socket
import subprocess
import unicodedata
import urllib.request
import wave

import numpy as np

base=Path(__file__).resolve().parent
project=base.parents[2]
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
plan=read(base/'plan.json'); runner=read(base/'runner.json')
report=read(base/'native-http/reports/asr-followup.json'); summary=read(base/'summary.json')
assert all(r['status']=='diagnostic_completed' for r in [runner,report,summary])
assert runner['source_sha256']==sha(base/'run.py')
assert plan['source_sha256']==sha(base/'prepare.py')
assert summary['source_sha256']==sha(base/'summarize.py')
assert runner['plan_sha256']==report['plan_sha256']==summary['plan_sha256']==sha(base/'plan.json')
assert summary['native_report_sha256']==sha(base/'native-http/reports/asr-followup.json')
assert len(runner['steps'])==3 and all(s['status']=='completed' and s['exit_code']==0 for s in runner['steps'])
assert report['owned_helper_exit_code']==0 and report['temporary_request_cleanup'] and report['production_and_config_preserved']
assert runner['production_preserved'] and runner['protected_inputs_preserved']
previous=base.parent/'asr-reference-20261107'
assert plan['source_plan_sha256']==sha(previous/'plan.json')
assert plan['baseline_report_sha256']==sha(previous/'native-http/reports/asr-followup.json')
old={r['id']:r for r in read(previous/'native-http/reports/asr-followup.json')['results']}
collector=read(base/'collector-provenance.json')
assert collector['original_sha256']==sha(collector['source'])
value=Path(collector['source']).read_text()
for before,after in collector['exact_replacements']:
    assert value.count(before)==1; value=value.replace(before,after)
assert value==(base/'run-native.py').read_text() and collector['generated_sha256']==sha(base/'run-native.py')
actual={r['id']:r for r in report['results']}
assert len(actual)==len(report['results'])==plan['expected_requests']==28
assert set(actual)=={r['id'] for r in plan['cases']}
assert all(r['http_status']==200 for r in actual.values())
assert len(plan['source_cases'])==len(summary['rows'])==12


def payload(path):
    with wave.open(str(path),'rb') as f:
        assert (f.getnchannels(),f.getsampwidth(),f.getframerate(),f.getcomptype())==(1,2,16000,'NONE')
        return f.readframes(f.getnframes())


def cut_audio(data):
    audio=np.frombuffer(data,dtype='<i2').astype(np.float64)/32768
    rms=np.array([np.sqrt(np.mean(audio[i:i+320]**2)) for i in range(0,len(audio)-319,160)])
    threshold=max(10**(-42/20),float(np.percentile(rms,95))*.1)
    groups=[]; cursor=0
    for quiet,values in itertools.groupby(rms<=threshold):
        length=len(list(values))
        if quiet: groups.append((cursor*160,(cursor+length-1)*160+320))
        cursor+=length
    cuts=[0]
    while len(audio)-cuts[-1]>288000:
        lower,upper=cuts[-1]+128000,min(cuts[-1]+288000,len(audio)-64000)
        choices=[r for r in groups if r[1]-r[0]>=3200 and lower<=(r[0]+r[1])//2<=upper]
        if choices:
            a,b=sorted(choices,key=lambda r:(-(r[1]-r[0]),r[0]))[0]
            cuts.append((a+b)//2)
        else:
            centers=np.arange(len(rms))*160+160
            indexes=np.flatnonzero((centers>=max(lower,upper-48000))&(centers<=upper))
            cuts.append(int(centers[indexes[np.argmin(rms[indexes])]]))
    return cuts+[len(audio)],threshold


def units(text):
    text=unicodedata.normalize('NFKC',text).casefold()
    pieces=re.split(r'([\u3400-\u4dbf\u4e00-\u9fff])',text)
    return [unit for piece in pieces for unit in re.findall(r'[^\W_]+',piece)]


def edit(a,b):
    matrix=[[0]*(len(b)+1) for _ in range(len(a)+1)]
    for i in range(len(a)+1):matrix[i][0]=i
    for j in range(len(b)+1):matrix[0][j]=j
    for i in range(1,len(a)+1):
        for j in range(1,len(b)+1):matrix[i][j]=min(matrix[i-1][j]+1,matrix[i][j-1]+1,matrix[i-1][j-1]+(a[i-1]!=b[j-1]))
    return matrix[-1][-1]


new_wavs=[]
for case,row in zip(plan['source_cases'],summary['rows'],strict=True):
    source=case['source']; assert source['id']==row['id']
    assert sha(source['audio'])==source['audio_sha256']
    pcm=payload(source['audio']); assert hashlib.sha256(pcm).hexdigest()==source['pcm_sha256']
    cuts,threshold=cut_audio(pcm)
    assert cuts==case['boundaries_samples'] and threshold==case['threshold_rms']
    chunks=case['chunks']; assert len(chunks)==len(cuts)-1
    assert [c['start_sample'] for c in chunks]==cuts[:-1] and [c['end_sample'] for c in chunks]==cuts[1:]
    restored=bytearray()
    for c in chunks:
        assert sha(c['audio'])==c['audio_sha256']
        data=payload(c['audio']); assert hashlib.sha256(data).hexdigest()==c['pcm_sha256']
        assert data==pcm[c['start_sample']*2:c['end_sample']*2]
        restored.extend(data)
        if not c['reused_original_request']:
            decoded=subprocess.check_output(['/opt/homebrew/bin/ffmpeg','-nostdin','-hide_banner','-loglevel','error',
                '-i',c['audio'],'-map','0:a:0','-vn','-ar','16000','-ac','1','-c:a','pcm_s16le','-f','s16le','pipe:1'],timeout=15)
            assert decoded==data
            new_wavs.append({'file':str(Path(c['audio']).relative_to(base)),'sha256':c['audio_sha256'],'pcm_sha256':c['pcm_sha256']})
    assert bytes(restored)==pcm
    before=actual[case['baseline_request_id']]['response']['text']
    after=' '.join(actual[c['request_id']]['response']['text'].strip() for c in chunks if actual[c['request_id']]['response']['text'].strip())
    assert before==old[source['id']]['response']['text']==row['baseline_text'] and after==row['candidate_text']
    assert row['baseline_edits']==edit(units(source['reference']),units(before))
    assert row['candidate_edits']==edit(units(source['reference']),units(after))
    assert row['edit_delta']==row['candidate_edits']-row['baseline_edits']
    assert row['reference_unit_count']==len(units(source['reference']))
    assert row['candidate_scripts']=={'han':bool(re.search(r'[\u3400-\u4dbf\u4e00-\u9fff]',after)),'latin':bool(re.search('[a-z]',after.casefold()))}
assert len(new_wavs)==summary['additional_segment_requests']==16
assert summary['baseline_total_edits']==sum(r['baseline_edits'] for r in summary['rows'])==154
assert summary['candidate_total_edits']==sum(r['candidate_edits'] for r in summary['rows'])==92
assert summary['cases_with_more_edits']==sum(r['edit_delta']>0 for r in summary['rows'])==2
assert summary['cases_with_fewer_edits']==sum(r['edit_delta']<0 for r in summary['rows'])==4
assert not summary['pilot_passed'] and not summary['candidate_promoted']
assert not summary['pilot_gate']['no_case_has_more_mixed_unit_edits']
assert summary['pilot_gate']['all_eight_mixed_candidates_contain_both_scripts']
assert not list((base/'native-http/tmp').glob('request-*'))
with socket.socket() as probe: assert probe.connect_ex(('127.0.0.1',report['port']))!=0
processes=subprocess.check_output(['/bin/ps','-axo','pid=,pgid=,command='],text=True).splitlines()
owned=[str(s['owned_group_pid']) for s in runner['steps']]
assert not any(p in line.split()[:2] for line in processes for p in owned)
assert not any(str(report['owned_helper_pid'])==line.split()[0] for line in processes)
formal=read(project/'docs/evidence/robustness-2026-11-06/model-preservation/source-sha256.json')
assert formal=={p:sha(project/p) for p in formal}
protected=read(base.parent/'power-kernel-20261107/generation-build/build.json')['preserved_input_sha256']
assert protected=={p:sha(p) for p in protected}
preserved=read(base.parent/'model-preservation-20261107/fixed-result.json')
assert preserved['source_files_before']=={p:sha(p) for p in preserved['source_files_before']}
assert preserved['frozen_before']=={p:sha(p) for p in preserved['frozen_before']}
production=Path.home()/'Library/Application Support/CyberAudio'
with urllib.request.urlopen('http://127.0.0.1:8765/ready',timeout=5) as f:state=json.load(f)
current={'instance':state['instance'],'status':state['status'],'busy':state['busy'],
         'config_sha256':sha(production/'config/service.json'),'token_sha256':sha(production/'config/api-token')}
assert current==runner['production_before']==runner['production_after']
for k,v in report['production_before'].items():assert current[k]==v==report['production_after'][k]
helper=project/'.context/robustness/model-preservation-20261107/final-bytes-model-preservation/CyberAudio.app/Contents/Helpers/CyberAudioService'
assert sha(helper)==report['helper_sha256']==plan['helper_sha256']
for p in base.glob('*.py'):ast.parse(p.read_text())
result={'status':'verified','verified_at':datetime.now(timezone.utc).isoformat(),'model_loaded_by_verifier':False,'inference_rerun':False,
        'source_cases':12,'all_original_baseline_texts_reproduced':True,'all_boundaries_recomputed_without_transcripts':True,
        'new_segment_wavs':new_wavs,'all_source_pcm_exactly_reconstructed':True,'all_new_wav_production_decode_payloads_exact':True,
        'actual_http_requests':28,'diagnostic_scores_independently_recomputed':True,'baseline_edits':154,'candidate_edits':92,
        'pilot_passed':False,'more_edit_cases':2,'candidate_promoted':False,'formal_source_or_defaults_changed':False,
        'formal_source_hashes_verified':len(formal),'original_model_files_verified':len(preserved['source_files_before']),
        'frozen_files_verified':len(preserved['frozen_before']),'protected_inputs_verified':len(protected),
        'owned_processes_absent':True,'listener_closed':True,'request_directories_empty':True,'production_preserved':True,
        'limits':summary['limits']}
out=base/'verification.json';assert not out.exists()
out.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ['new_segment_wavs','limits']},indent=2))
