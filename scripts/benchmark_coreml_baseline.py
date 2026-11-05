"""Development-only paired ASR benchmark against the running CyberAudio service.

Run sequentially on the inference Mac. Reads the API token privately from the
existing configuration, never changes service state, and saves each result.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import time
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='Existing CyberAudio configuration root')
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((args.root / 'config/service.json').read_text())
    token = (args.root / 'config/api-token').read_text().strip()
    base = f'http://127.0.0.1:{int(config.get("port", 8765))}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, payload=None, content_type=None):
        headers = {'Authorization': 'Bearer ' + token}
        if content_type:
            headers['Content-Type'] = content_type
        with opener.open(urllib.request.Request(base + path, data=payload, headers=headers), timeout=150) as response:
            return json.load(response)

    ready = request('/ready')
    if ready.get('status') != 'ready' or ready.get('busy') or ready.get('updating'):
        raise RuntimeError('Production service is not idle and ready; benchmark was not started')
    models = request('/v1/models')
    corpus = json.loads(args.corpus.read_text())
    if not corpus or len({item['id'] for item in corpus}) != len(corpus):
        raise ValueError('Empty corpus or duplicate IDs')
    report = {'status': 'running', 'model': ready['model'], 'models': models, 'ready_before': ready,
              'platform': platform.platform(), 'results': []}
    args.report.parent.mkdir(parents=True, exist_ok=True)

    def save():
        temporary = args.report.with_suffix('.partial.json')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(args.report)

    save()
    try:
        for item in corpus:
            audio_path = (args.corpus.parent / item['audio']).resolve()
            if not audio_path.is_relative_to(args.corpus.parent.resolve()):
                raise ValueError('Corpus audio escapes root')
            audio = audio_path.read_bytes()
            digest = hashlib.sha256(audio).hexdigest()
            if digest != item['audio_sha256']:
                raise ValueError('Corpus audio checksum mismatch: ' + item['id'])
            boundary = 'CyberAudioBenchmark' + uuid.uuid4().hex
            # Auto detection matches the SenseVoice probe's language policy.
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\nauto\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.wav"\r\n'
                    'Content-Type: audio/wav\r\n\r\n').encode() + audio + f'\r\n--{boundary}--\r\n'.encode()
            started = time.monotonic()
            result = request('/v1/audio/transcriptions', body, 'multipart/form-data; boundary=' + boundary)
            elapsed_ms = (time.monotonic() - started) * 1000
            report['results'].append({'id': item['id'], 'repetition': 0, 'audio_sha256': digest,
                'reference': item['reference'], 'language': item['language'], 'text': result['text'],
                'milliseconds': result['processing_ms'], 'http_milliseconds': elapsed_ms, 'response': result})
            save()
            print(f'{len(report["results"])}/{len(corpus)} {item["id"]}: {result["processing_ms"]:.1f} ms', flush=True)
        after = request('/ready')
        if after.get('instance') != ready.get('instance'):
            raise RuntimeError('Production instance changed during benchmark')
        report.update(status='completed', ready_after=after)
    except Exception as exc:
        report.update(status='failed', error=type(exc).__name__)
        raise
    finally:
        save()


if __name__ == '__main__':
    main()
