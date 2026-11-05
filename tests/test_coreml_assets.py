"""Development-only preparation must never accept corrupt model assets."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/prepare_coreml_models.py'


def load_script():
    spec = importlib.util.spec_from_file_location('coreml_assets', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_or_corrupt_assets_fail_offline_verification(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), '--root', str(tmp_path), '--verify-only'], capture_output=True)
    assert result.returncode != 0
    assert b'Missing or invalid' in result.stderr


def test_verification_rejects_correct_size_wrong_digest(tmp_path):
    module = load_script()
    (tmp_path / 'weight.bin').write_bytes(b'bad!')
    item = {'path': 'weight.bin', 'size': 4, 'sha256': hashlib.sha256(b'good').hexdigest()}
    assert not module.valid_file(tmp_path, item)


def test_verification_checks_git_object_hash_for_non_lfs_files(tmp_path):
    module = load_script()
    payload = b'config'
    (tmp_path / 'config.json').write_bytes(payload)
    item = {'path': 'config.json', 'size': 6, 'git_blob_sha1': hashlib.sha1(b'blob 6\0' + payload).hexdigest()}
    assert module.valid_file(tmp_path, item)
    (tmp_path / 'config.json').write_bytes(b'Config')
    assert not module.valid_file(tmp_path, item)


def test_verification_rejects_traversal_and_symlink_escape(tmp_path):
    module = load_script()
    assert not module.valid_file(tmp_path, {'path': '../escape', 'size': 0, 'sha256': '0' * 64})
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.write_bytes(b'good')
    (tmp_path / 'link').symlink_to(outside)
    assert not module.valid_file(tmp_path, {'path': 'link', 'size': 4, 'sha256': hashlib.sha256(b'good').hexdigest()})


@contextmanager
def range_server(payload, *, wrong_range=False, interrupt_first=False):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()

        def do_GET(self):
            start, end = map(int, self.headers['Range'].removeprefix('bytes=').split('-'))
            requests.append((start, end))
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {start + int(wrong_range)}-{end}/{len(payload)}')
            self.send_header('Content-Length', str(end - start + 1))
            self.end_headers()
            data = payload[start:end + 1]
            if interrupt_first and len(requests) == 1:
                data = data[:10]
            self.wfile.write(data)
            self.close_connection = True

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/asset', requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_range_download_preserves_prefix_and_resumes_interrupted_response(tmp_path):
    module = load_script()
    payload = bytes(range(100))
    partial = tmp_path / 'range.partial'
    partial.write_bytes(payload[10:15])
    with range_server(payload, interrupt_first=True) as (url, requests):
        module.download_ranges(url, partial, len(payload), [], start=10, end=80)
    assert partial.read_bytes() == payload[10:81]
    assert requests == [(15, 80), (25, 80)]


def test_range_download_rejects_wrong_content_range(tmp_path):
    module = load_script()
    partial = tmp_path / 'range.partial'
    with range_server(b'public model', wrong_range=True) as (url, _requests):
        with pytest.raises(RuntimeError, match='Invalid range response'):
            module.download_ranges(url, partial, 12, [])
    assert not partial.exists()
