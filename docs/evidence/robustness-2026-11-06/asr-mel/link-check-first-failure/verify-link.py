"""Verify the model-free caller replaced only the preceding caller object."""
from pathlib import Path
import hashlib
import json

base = Path(__file__).resolve().parent
project = base.parents[2]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
old = base.parent / 'asr-reference-20261107/front-build/Objects.LinkFileList'
new = base / 'native-build/Objects.LinkFileList'
a, b = old.read_text().splitlines(), new.read_text().splitlines()
old_main = str(base.parent / 'asr-reference-20261107/front-build/ASRFrontProbe.o')
new_main = str(base / 'native-build/MelDSPProbe.o')
assert len(a) == len(b)
assert [(x, y) for x, y in zip(a, b, strict=True) if x != y] == [(old_main, new_main)]
protected = read(base.parent / 'power-kernel-20261107/generation-build/build.json')['preserved_input_sha256']
assert (set(a) - {old_main}).issubset(protected)
assert all(sha(p) == protected[p] for p in a if p != old_main)
result = {'status': 'verified', 'model_loaded': False, 'source_sha256': sha(Path(__file__)),
          'previous_list_sha256': sha(old), 'new_list_sha256': sha(new),
          'total_objects': len(b), 'single_caller_object_replaced': True,
          'shared_objects_preserved': len(b) - 1,
          'new_object_sha256': sha(new_main)}
path = base / 'link-control.json'
assert not path.exists()
path.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
