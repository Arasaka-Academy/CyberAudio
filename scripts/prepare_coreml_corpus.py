"""Extract 50 real Chinese + 50 English FLEURS test recordings (no synthetic speech).

Development dependency: pyarrow==26.0.1. Output excludes mixed-speech/entity claims.
Downloads immutable Parquet sources with resumable ranges and verifies SHA256
before extracting recordings. FLEURS packs all audio into large dictionary pages.
"""
import argparse
import hashlib
import json
from pathlib import Path
from prepare_coreml_models import download_ranges, valid_file

REVISION = '168de341b3db6859a9bac1c50a2ef5e3b47647e0'
SOURCES = {
    'zh': ('cmn_hans_cn', 695674033, '87c0aebbe183f3a36ac87b5c3421b6ab57036824744ff695029a3f858e7622fd'),
    'en': ('en_us', 401722686, '6428a4d04d3aac29e16b45e039bb1470a8bd7aa334cf92f7984c9c520d1f234d'),
}


def prepare_source(root, language, proxy):
    config, size, digest = SOURCES[language]
    root.mkdir(parents=True, exist_ok=True)
    item = {'path': language + '.parquet', 'size': size, 'sha256': digest}
    if not valid_file(root, item):
        partial = root / (item['path'] + '.partial')
        url = f'https://huggingface.co/datasets/google/fleurs/resolve/{REVISION}/{config}/test/0000.parquet'
        download_ranges(url, partial, size, ['--proxy', proxy] if proxy else [])
        if not valid_file(root, {**item, 'path': partial.name}):
            raise RuntimeError('Dataset source checksum mismatch: ' + language)
        partial.replace(root / item['path'])
    return root / item['path']


def write_samples(root, language, rows):
    items = []
    config = SOURCES[language][0]
    for index, row in enumerate(rows[:50]):
        audio = row['audio']['bytes']
        # FLEURS id identifies the prompt, not a unique recording. Multiple
        # speakers can share an id; the immutable row offset disambiguates them.
        ident = f'{language}-{index:03d}-{row["id"]}'
        filename = ident + '.wav'
        (root / filename).write_bytes(audio)
        items.append({'id': ident, 'audio': filename, 'reference': row.get('raw_transcription') or row['transcription'],
            'language': language, 'audio_sha256': hashlib.sha256(audio).hexdigest(), 'kind': 'human', 'entities': [],
            'source': f'google/fleurs@{REVISION}:{config}/test/row-{index}',
            'dataset_id': row['id'], 'dataset_audio_path': row['audio'].get('path')})
    return items


def main():
    import pyarrow.parquet as pq
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--proxy', default='')
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    items = []
    for language in SOURCES:
        source = prepare_source(args.root / '.sources', language, args.proxy)
        parquet = pq.ParquetFile(source)
        rows = next(parquet.iter_batches(batch_size=50, use_threads=False,
            columns=['id', 'audio', 'raw_transcription', 'transcription'])).to_pylist()
        if len(rows) < 50:
            raise RuntimeError('Dataset contains fewer than 50 samples')
        items.extend(write_samples(args.root, language, rows))
        print(f'{language}: extracted 50 human recordings', flush=True)
    (args.root / 'corpus.json').write_text(json.dumps(items, ensure_ascii=False, indent=2) + '\n')
    (args.root / 'provenance.json').write_text(json.dumps({'dataset': 'google/fleurs', 'revision': REVISION,
        'samples': 100, 'license': 'CC-BY-4.0', 'mixed_speech': False, 'entities_annotated': False,
        'source_lfs_sha256': {lang: value[2] for lang, value in SOURCES.items()},
        'source_full_parquet_hash_verified': True}, indent=2) + '\n')


if __name__ == '__main__':
    main()
