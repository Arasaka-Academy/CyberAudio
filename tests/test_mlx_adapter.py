"""Exercise the adapter command against pristine and tampered pinned SDK files."""
import hashlib
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "Sources/MLXAudioTTS/Models/Qwen3TTS/Qwen3TTS.swift",
    "Sources/MLXAudioSTT/Models/Qwen3ASR/Qwen3ASR.swift",
)


@pytest.fixture
def checkout(tmp_path):
    scratch = Path(os.environ.get("CYBERAUDIO_NATIVE_SCRATCH", ROOT / ".context/native-build"))
    sdk = scratch / "checkouts/mlx-audio-swift"
    if not sdk.is_dir():
        pytest.skip("Resolve the pinned SDK with make native-test before testing its adapter")
    for relative in SOURCES:
        original = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=sdk)
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(original)
    return tmp_path


def patch(checkout):
    return subprocess.run(
        ["swift", str(ROOT / "scripts/patch_mlx_audio.swift"), str(checkout)],
        capture_output=True, timeout=30,
    )


def test_reapplying_adapter_is_idempotent(checkout):
    first = patch(checkout)
    assert first.returncode == 0, first.stderr.decode()
    prepared = [(checkout / source).read_bytes() for source in SOURCES]
    second = patch(checkout)
    assert second.returncode == 0, second.stderr.decode()
    assert [(checkout / source).read_bytes() for source in SOURCES] == prepared


def test_existing_managed_checkout_upgrades_without_changing_asr(checkout):
    assert patch(checkout).returncode == 0
    path = checkout / SOURCES[0]
    previous = path.read_text().replace(
        'if ["chinese", "auto"].contains(language.lowercased()),\n               let dialectMap = talkerConfig.spkIsDialect,',
        'if let dialectMap = talkerConfig.spkIsDialect,',
    ).encode()
    assert hashlib.sha256(previous).hexdigest() == "0fcfeaaadd8eb6bb793d985162450968fa1b4e6c8d198962a988e8e4aaa55426"
    asr = (checkout / SOURCES[1]).read_bytes()
    path.write_bytes(previous)
    updated = patch(checkout)
    assert updated.returncode == 0, updated.stderr.decode()
    assert path.read_bytes() != previous
    assert (checkout / SOURCES[1]).read_bytes() == asr
    prepared = path.read_bytes()
    assert patch(checkout).returncode == 0
    assert path.read_bytes() == prepared


@pytest.mark.parametrize("source", SOURCES)
def test_adapter_refuses_tampered_sdk_without_overwriting_it(checkout, source):
    assert patch(checkout).returncode == 0
    path = checkout / source
    tampered = path.read_bytes() + b"\n// Unexpected local modification\n"
    path.write_bytes(tampered)
    assert patch(checkout).returncode != 0
    assert path.read_bytes() == tampered
