"""Native experiments must not write through to production models."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[1] / "scripts/prepare_qwen_swift_assets.py"
    spec = importlib.util.spec_from_file_location("qwen_swift_assets", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def assets(tmp_path):
    helper = module()
    root = tmp_path / "production"
    (root / "config").mkdir(parents=True)
    configuration = {}
    for kind, (repository, revision) in helper.IDENTITIES.items():
        source = root / kind
        source.mkdir()
        config = {"model_type": "qwen3_" + kind, "quantization": {"bits": 8}}
        if kind == "tts":
            config.update(tts_model_type="custom_voice", tts_model_size="1b7")
        (source / "config.json").write_text(json.dumps(config))
        (source / "model.safetensors").write_bytes(b"fixed test weights")
        checksum = hashlib.sha256(b"fixed test weights").hexdigest()
        (source / "cyberaudio-manifest.json").write_text(json.dumps({"repository": repository,
            "revision": revision, "files": [{"path": "model.safetensors", "sha256": checksum}]}))
        configuration[kind + "_dir"] = str(source)
    (root / "config/service.json").write_text(json.dumps(configuration))
    return helper, root, tmp_path / "staged"


def test_stages_independent_files_and_keeps_source_unchanged(assets):
    helper, source, target = assets
    helper.prepare(source, target)
    manifest = json.loads((target / "assets.lock.json").read_text())
    assert all(value["weights_match_production"] for value in manifest["models"].values())
    (target / "asr/model.safetensors").write_bytes(b"changed experiment")
    (target / "asr/tokenizer.json").write_text("derived tokenizer")
    assert (source / "asr/model.safetensors").read_bytes() == b"fixed test weights"
    assert not (source / "asr/tokenizer.json").exists()


def test_rejects_staging_inside_production_model(assets):
    helper, source, _ = assets
    with pytest.raises(ValueError, match="separate"):
        helper.prepare(source, source / "asr/experiment")


def test_rejects_existing_staging_symlink_to_production(assets):
    helper, source, target = assets
    target.mkdir()
    (target / "asr").symlink_to(source / "asr", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        helper.prepare(source, target)


def test_rejects_corrupt_pinned_production_weights(assets):
    helper, source, target = assets
    (source / "asr/model.safetensors").write_bytes(b"corrupted weights")
    with pytest.raises(ValueError, match="checksum mismatch"):
        helper.prepare(source, target)
    assert not (target / "assets.lock.json").exists()
