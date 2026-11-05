"""Development-time asset preparation. The native probe has no Python dependency."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse

LOCK = Path(__file__).resolve().parents[1] / 'Experiments/CoreML/assets.lock.json'


def valid_file(root, item):
    root = root.resolve()
    relative = Path(item['path'])
    if relative.is_absolute() or '..' in relative.parts:
        return False
    path = root / relative
    if not path.resolve().is_relative_to(root) or not path.is_file() or path.stat().st_size != item['size']:
        return False
    digest = hashlib.sha256() if 'sha256' in item else hashlib.sha1()
    if 'sha256' not in item:
        digest.update(f"blob {item['size']}\0".encode())
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest() == item.get('sha256', item.get('git_blob_sha1'))


def download_ranges(url, partial, size, routes, start=0, end=None):
    """Resume a byte interval with bounded, Content-Range-validated transfers."""
    end = size - 1 if end is None else end
    if not 0 <= start <= end < size:
        raise ValueError('Invalid byte interval')
    prefix = partial.stat().st_size if partial.exists() else 0
    if prefix > end - start + 1:
        raise RuntimeError('Oversized partial asset: ' + str(partial))
    resolved = subprocess.run(['curl', '--http1.1', '-fLsSI', *routes, '--connect-timeout', '15', '--max-time', '45',
        '-o', '/dev/null', '-w', '%{url_effective}', url], capture_output=True, check=True).stdout.decode()
    parts = partial.with_name(partial.name + '.ranges')
    parts.mkdir(exist_ok=True)
    block = 16 * 1024 * 1024

    def fetch_range(offset):
        last = min(offset + block - 1, end)
        length = last - offset + 1
        path = parts / str(offset)
        if path.exists() and path.stat().st_size == length:
            return path
        incoming = parts / (str(offset) + '.incoming')
        headers = parts / (str(offset) + '.headers')
        progress = parts / (str(offset) + '.progress')
        # Retain an interrupted, range-validated prefix across invocations.
        if not progress.exists() and incoming.exists() and headers.exists():
            found = re.findall(r'content-range:\s*bytes (\d+)-(\d+)/(\d+)', headers.read_text().lower())
            if found and tuple(map(int, found[-1])) == (offset, last, size) and incoming.stat().st_size <= length:
                incoming.replace(progress)
        for attempt in range(8):
            existing = progress.stat().st_size if progress.exists() else 0
            if existing == length:
                progress.replace(path)
                return path
            if existing > length:
                raise RuntimeError('Oversized range prefix')
            current = offset + existing
            incoming.unlink(missing_ok=True)
            route = ['--noproxy', '*'] if attempt % 2 == 0 else routes
            subprocess.run(['curl', '--http1.1', '-fLsS', *route, '--connect-timeout', '12', '--max-time', '90',
                '--range', f'{current}-{last}', '-D', str(headers), '-o', str(incoming), resolved], capture_output=True)
            found = re.findall(r'content-range:\s*bytes (\d+)-(\d+)/(\d+)', headers.read_text().lower() if headers.exists() else '')
            if found and tuple(map(int, found[-1])) == (current, last, size) and incoming.exists() and incoming.stat().st_size <= last - current + 1:
                with progress.open('ab') as output, incoming.open('rb') as source:
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
        if progress.exists() and progress.stat().st_size == length:
            progress.replace(path)
            return path
        raise RuntimeError('Invalid range response for ' + str(partial))

    starts = list(range(start + prefix, end + 1, block))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch_range, starts))
    with partial.open('ab') as output:
        for offset in starts:
            with (parts / str(offset)).open('rb') as source:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)


def prepare(root, proxy, verify_only=False, component='all', lock_path=LOCK):
    lock = json.loads(lock_path.read_text())
    root.mkdir(parents=True, exist_ok=True)
    files = [item for item in lock['files'] if component == 'all'
             or (component == 'asr' and item['path'].startswith('sensevoice/'))
             or (component == 'tts' and not item['path'].startswith('sensevoice/'))]
    failures = [item for item in files if not valid_file(root, item)]
    if verify_only:
        if failures:
            raise RuntimeError('Missing or invalid: ' + ', '.join(item['path'] for item in failures))
        return
    routes = ['--proxy', proxy] if proxy else []

    def fetch(item):
        destination = root / item['path']
        if not destination.resolve().is_relative_to(root.resolve()):
            raise RuntimeError('Asset path escapes root')
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + '.partial')
        url = 'https://huggingface.co/' + item['repository'] + '/resolve/' + item['revision'] + '/' + urllib.parse.quote(item['source'])
        candidate = {**item, 'path': str(partial.relative_to(root))}
        if item['size'] >= 32 * 1024 * 1024:
            download_ranges(url, partial, item['size'], routes)
        else:
            for attempt in range(4):
                route = routes if attempt % 2 == 0 else ['--noproxy', '*']
                subprocess.run(['curl', '-fLsS', '--retry', '1', '--retry-delay', '1', '--connect-timeout', '12',
                    '--max-time', '60', *route, '--continue-at', '-', '--output', str(partial), url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                if valid_file(root, candidate):
                    break
        if not valid_file(root, candidate):
            raise RuntimeError('Download/checksum failed: ' + item['path'])
        partial.replace(destination)
        print('verified ' + item['path'], flush=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        # A failed metadata request must not cancel all queued assets while a
        # multi-gigabyte weight finishes. Retain every independent successful
        # transfer so a retry only downloads what is still missing.
        pending = [pool.submit(fetch, item) for item in failures]
        errors = []
        for future in as_completed(pending):
            try:
                future.result()
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError('; '.join(errors))
    prepare(root, proxy, verify_only=True, component=component, lock_path=lock_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--proxy', default='')
    parser.add_argument('--lock', type=Path, default=LOCK)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--component', choices=['all', 'asr', 'tts'], default='all')
    args = parser.parse_args()
    try:
        prepare(args.root, args.proxy, args.verify_only, args.component, args.lock)
    except (RuntimeError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
