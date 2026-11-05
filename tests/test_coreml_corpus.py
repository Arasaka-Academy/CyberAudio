"""Dataset prompt IDs must not overwrite distinct human recordings."""
import hashlib
import importlib.util
from pathlib import Path


def test_repeated_prompt_id_keeps_both_recordings_and_stable_provenance(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / 'scripts/prepare_coreml_corpus.py'
    monkeypatch.syspath_prepend(str(script.parent))
    spec = importlib.util.spec_from_file_location('coreml_corpus', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = [{'id': 7, 'audio': {'bytes': audio, 'path': name}, 'raw_transcription': '你好'}
            for name, audio in [('first.wav', b'first recording'), ('second.wav', b'second recording')]]
    items = module.write_samples(tmp_path, 'zh', rows)
    assert len({item['id'] for item in items}) == 2
    assert len({item['source'] for item in items}) == 2
    for item, row in zip(items, rows):
        payload = (tmp_path / item['audio']).read_bytes()
        assert payload == row['audio']['bytes']
        assert item['audio_sha256'] == hashlib.sha256(payload).hexdigest()
    assert module.write_samples(tmp_path, 'zh', rows) == items
