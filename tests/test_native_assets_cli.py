"""The App's machine-readable preparation boundary preserves private settings."""
import json
import os
from pathlib import Path
import subprocess
import fcntl
import selectors
import socket
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def assets_tool():
    scratch = Path(os.environ.get("CYBERAUDIO_NATIVE_SCRATCH", ROOT / ".context/native-build"))
    binary = scratch / "arm64-apple-macosx/release/CyberAudioAssets"
    if not binary.is_file():
        pytest.skip("Build the bundled asset tool with make native-test before CLI tests")
    return binary


def run(tool, command, root, *options):
    return subprocess.run([str(tool), command, "--root", str(root), *options],
                          capture_output=True, text=True, timeout=15)


def test_status_and_ffmpeg_selection_preserve_configuration_and_token(assets_tool, tmp_path):
    prepared = run(assets_tool, "prepare", tmp_path, "--port", "14401", "--ffmpeg", "/usr/bin/true")
    assert prepared.returncode == 0, prepared.stderr
    config = tmp_path / "config/service.json"
    token = tmp_path / "config/api-token"
    original = json.loads(config.read_bytes())
    private = token.read_bytes()
    status = run(assets_tool, "status", tmp_path, "--json", "true")
    assert status.returncode == 0, status.stderr
    value = json.loads(status.stdout)
    assert value["event"] == "status" and value["missing_files"] == 23
    assert not value["models_ready"] and not value["ffmpeg_ready"]
    assert value["verified_bytes"] == 0 and value["total_bytes"] == 5_547_997_530
    assert token.read_bytes() == private and json.loads(config.read_bytes()) == original
    assert private.strip().decode() not in status.stdout

    selected = run(assets_tool, "set-ffmpeg", tmp_path, "--ffmpeg", "/opt/homebrew/bin/ffmpeg", "--json", "true")
    assert selected.returncode == 0, selected.stderr
    assert json.loads(selected.stdout)["ffmpeg_ready"]
    assert json.loads(config.read_bytes()) == {**original, "ffmpeg": "/opt/homebrew/bin/ffmpeg"}
    assert token.read_bytes() == private
    saved = config.read_bytes()
    rejected = run(assets_tool, "set-ffmpeg", tmp_path, "--ffmpeg", "/usr/bin/true", "--json", "true")
    assert rejected.returncode != 0 and json.loads(rejected.stdout)["event"] == "error"
    assert config.read_bytes() == saved and token.read_bytes() == private


def test_json_failure_is_structured_and_does_not_echo_private_paths(assets_tool, tmp_path):
    private_root = tmp_path / "private-sentinel-never-print"
    result = run(assets_tool, "status", private_root, "--json", "true")
    assert result.returncode != 0
    error = json.loads(result.stdout)
    assert error["event"] == "error" and error["code"] and error["message"]
    assert "private-sentinel-never-print" not in result.stdout + result.stderr


def test_owned_download_cancels_if_application_crashes(assets_tool, tmp_path):
    assert run(assets_tool, "prepare", tmp_path).returncode == 0
    with socket.socket() as proxy:
        proxy.bind(("127.0.0.1", 0))
        proxy.listen()
        proxy.settimeout(5)
        launcher = """import os,subprocess,sys,time
child=subprocess.Popen([sys.argv[1],'download','--root',sys.argv[2],'--json','true','--proxy',sys.argv[3],'--parent-pid',str(os.getpid())])
print(child.pid,flush=True)
time.sleep(30)
"""
        parent = subprocess.Popen([sys.executable, "-c", launcher, str(assets_tool), str(tmp_path),
                                   f"http://127.0.0.1:{proxy.getsockname()[1]}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child_pid = int(parent.stdout.readline())
        try:
            connection, _ = proxy.accept()
            with connection:
                parent.kill()
                parent.wait(timeout=3)
                selector = selectors.DefaultSelector()
                selector.register(parent.stdout, selectors.EVENT_READ)
                output = b""
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if selector.select(timeout=0.2):
                        data = os.read(parent.stdout.fileno(), 16384)
                        if not data:
                            break
                        output += data
                else:
                    pytest.fail("Owned asset helper survived its application")
                selector.close()
                records = [json.loads(line) for line in output.splitlines()]
                assert records[-1]["event"] == "error" and records[-1]["code"] == "cancelled"
            with (tmp_path / "tmp/asset-download.lock").open("rb") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=3)
            # Only this test's helper; leave other App processes untouched.
            try:
                os.kill(child_pid, 15)
            except ProcessLookupError:
                pass
