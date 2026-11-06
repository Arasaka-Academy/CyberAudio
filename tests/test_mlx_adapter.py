"""Exercise the adapter command against pristine and tampered pinned SDK files."""
import hashlib
import json
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


def tokenizer_loading_branch(source):
    return source.split(
        "        // Load speech tokenizer — check that it's a directory, not a stale file\n", 1,
    )[1].split("        // Load speaker encoder for base models when available\n", 1)[0]


def test_reapplying_adapter_is_idempotent(checkout):
    first = patch(checkout)
    assert first.returncode == 0, first.stderr.decode()
    prepared = [(checkout / source).read_bytes() for source in SOURCES]
    second = patch(checkout)
    assert second.returncode == 0, second.stderr.decode()
    assert [(checkout / source).read_bytes() for source in SOURCES] == prepared


@pytest.mark.parametrize("legacy_sha", [
    "0fcfeaaadd8eb6bb793d985162450968fa1b4e6c8d198962a988e8e4aaa55426",
    "0190fd2a02d6ce0eaa3732ca7b7d373be251747edc5d14a0d0b4fe8ce3c0dd6b",
])
def test_existing_managed_checkout_upgrades_without_changing_asr(checkout, legacy_sha):
    path = checkout / SOURCES[0]
    upstream_loading = tokenizer_loading_branch(path.read_text())
    assert patch(checkout).returncode == 0
    current = path.read_text()
    previous = current.replace(tokenizer_loading_branch(current), upstream_loading)
    if legacy_sha == "0fcfeaaadd8eb6bb793d985162450968fa1b4e6c8d198962a988e8e4aaa55426":
        previous = previous.replace(
            'if ["chinese", "auto"].contains(language.lowercased()),\n               let dialectMap = talkerConfig.spkIsDialect,',
            'if let dialectMap = talkerConfig.spkIsDialect,',
        )
    previous = previous.encode()
    assert hashlib.sha256(previous).hexdigest() == legacy_sha
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


@pytest.mark.parametrize("tokenizer_shape", ["file", "directory", "missing"])
def test_tts_loader_preserves_model_files_on_invalid_tokenizer(checkout, tokenizer_shape):
    assert patch(checkout).returncode == 0
    # Execute the SDK's actual filesystem branch with Foundation. Only the
    # unrelated MLX weight loading and speech decoder initialization are omitted.
    branch = tokenizer_loading_branch((checkout / SOURCES[0]).read_text())
    model_root = checkout / "models"
    model_root.mkdir()
    sentinel = model_root / "model.safetensors"
    sentinel.write_bytes(b"Existing model data must be preserved")
    tokenizer = model_root / "speech_tokenizer"
    if tokenizer_shape == "file":
        tokenizer.write_bytes(b"Unexpected user file")
    elif tokenizer_shape == "directory":
        tokenizer.mkdir()
        (tokenizer / "model.safetensors").write_bytes(b"Existing speech decoder")
    before = {
        str(path.relative_to(model_root)): path.read_bytes()
        for path in model_root.rglob("*") if path.is_file()
    }
    driver = checkout / "loader-filesystem.swift"
    driver.write_text(r'''
import Foundation
enum AudioGenerationError: Error { case modelNotInitialized(String) }
final class ProbeModel { var loaded = false }
func loadSpeechTokenizer(model: ProbeModel, path: URL) throws { model.loaded = true }
let model = ProbeModel()
let fm = FileManager.default
let modelDir = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
var rejected = false
do {
''' + branch + r'''
} catch AudioGenerationError.modelNotInitialized { rejected = true }
print(String(data: try JSONSerialization.data(withJSONObject:
    ["rejected": rejected, "loaded": model.loaded], options: [.sortedKeys]), encoding: .utf8)!)
''')
    result = subprocess.run(
        ["swift", str(driver), str(model_root)], capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert sentinel.is_file(), "Malformed speech tokenizer must not delete the model directory"
    after = {
        str(path.relative_to(model_root)): path.read_bytes()
        for path in model_root.rglob("*") if path.is_file()
    }
    assert after == before
    observation = json.loads(result.stdout.decode().splitlines()[-1])
    assert observation == {
        "rejected": tokenizer_shape == "file", "loaded": tokenizer_shape == "directory",
    }
