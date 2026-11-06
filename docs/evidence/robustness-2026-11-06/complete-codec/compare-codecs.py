"""Read complete codec vectors without loading either inference runtime."""
from pathlib import Path
import hashlib
import json
import struct

base = Path(__file__).resolve().parent
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
native_root = base / 'native-capture'
report = read(native_root / 'report.json')
reference_root = base.parent / 'reference-generation-20261107'
reference = read(reference_root / 'report.json')
assert report['status'] == reference['status'] == 'diagnostic_completed'
assert len(report['results']) == 6
output = base / 'code-comparison.json'
assert not output.exists()
directory = base / 'native-codes'
directory.mkdir()
results = []
for row in report['results']:
    assert row['original_candidate_tokens_exact'] and row['original_candidate_pcm_exact']
    vectors = []
    for item in row['steps']:
        path = native_root / row['id'] / item['file']
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == item['sha256']
        size = struct.unpack('<Q', data[:8])[0]
        info = json.loads(data[8:8+size])['codes']
        assert info['shape'] == [1,16] and info['dtype'] in {'I32','U32'}
        start,end = info['data_offsets']
        raw = data[8+size+start:8+size+end]
        vectors.append(list(struct.unpack('<16I', raw)))
    assert [v[0] for v in vectors] == row['tokens']
    path = directory / (row['id']+'.json')
    path.write_text(json.dumps(vectors)+'\n')
    previous = next(r for r in reference['results'] if r['id'] == row['id'])
    reference_path = reference_root / row['id'] / 'codes.json'
    assert sha(reference_path) == previous['codes_sha256']
    expected = read(reference_path)
    assert len(vectors) == len(expected) == row['token_count']
    differences = [{'step': step, 'group': group, 'native': a, 'reference': b,
                    'audio_frame': step < len(vectors)-1}
                   for step,(left,right) in enumerate(zip(vectors,expected,strict=True))
                   for group,(a,b) in enumerate(zip(left,right,strict=True)) if a!=b]
    results.append({'id':row['id'],'steps':len(vectors),'native_codes_sha256':sha(path),
                    'reference_codes_sha256':sha(reference_path),'all_groups_equal':not differences,
                    'main_tokens_equal':all(left[0]==right[0] for left,right in zip(vectors,expected,strict=True)),
                    'differences':differences})
summary={'status':'diagnostic_completed','native_report_sha256':sha(native_root/'report.json'),
         'reference_report_sha256':sha(reference_root/'report.json'),'cases':len(results),
         'steps':sum(r['steps'] for r in results),'codec_values':sum(r['steps']*16 for r in results),
         'all_original_candidate_pcm_and_main_tokens_exact':True,'results':results,
         'limits':['Two known-failure repetitions are deliberate repeats, not independent samples',
                   'Codec differences localize generation divergence but do not establish its numerical or sampling cause',
                   'No source/default/promotion or listening acceptance change']}
output.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
