"""Exercise the shipped local model-management boundary with isolated roots."""
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def model_tool():
    default = ROOT / ".context/native-build/arm64-apple-macosx/release/CyberAudioAssets"
    tool = Path(os.environ.get("CYBERAUDIO_ASSETS_TOOL", default))
    if not tool.is_file():
        pytest.skip("Build the native asset tool first")
    return tool


def invoke(tool, root, command, *options):
    return subprocess.run([str(tool), command, "--root", str(root), "--json", "true", *options],
                          capture_output=True, text=True, timeout=30)


def records(result):
    return [json.loads(line) for line in result.stdout.splitlines()]


def test_catalog_exposes_eight_pinned_variants_without_preparing_data(model_tool, tmp_path):
    root = tmp_path / "unprepared"
    result = invoke(model_tool, root, "catalog")
    assert result.returncode == 0, result.stdout
    catalog = records(result)[-1]
    assert catalog["event"] == "catalog"
    models = catalog["models"]
    assert len(models) == 8
    assert len({model["id"] for model in models}) == 8
    assert {(m["component"], m["parameters"], m["quantization"]) for m in models} == {
        (kind, size, bits) for kind in ["asr", "tts"] for size in ["0.6B", "1.7B"] for bits in ["4bit", "8bit"]
    }
    for model in models:
        assert model["total_bytes"] > 0
        assert len(model["revision"]) == 40
        assert "modelscope" in model["sources"]
    assert not root.exists()


def test_legacy_config_is_reused_and_background_status_does_not_change_selection(model_tool, tmp_path):
    assert invoke(model_tool, tmp_path, "prepare").returncode == 0
    config = tmp_path / "config/service.json"
    token = tmp_path / "config/api-token"
    legacy = json.loads(config.read_bytes())
    for key in ["asr_model_id", "tts_model_id", "models_dir"]:
        legacy.pop(key, None)
    legacy["https_url"] = "https://existing.invalid"
    config.write_text(json.dumps(legacy))
    before, private = config.read_bytes(), token.read_bytes()
    result = invoke(model_tool, tmp_path, "models")
    assert result.returncode == 0, result.stdout
    status = records(result)[-1]
    assert status["asr_model_id"] == "qwen3-asr-1.7b"
    assert status["tts_model_id"] == "qwen3-tts-1.7b"
    assert len(status["models"]) == 8
    assert config.read_bytes() == before
    assert token.read_bytes() == private
    result = invoke(model_tool, tmp_path, "status", "--model", "qwen3-asr-0.6b-4bit")
    assert result.returncode == 0
    assert [item["id"] for item in records(result)[-1]["models"]] == ["qwen3-asr-0.6b-4bit"]
    result = invoke(model_tool, tmp_path, "models", "--model", "unknown-model")
    assert result.returncode != 0 and records(result)[-1]["code"] == "invalid_model"


def test_model_delete_and_activation_reject_active_or_missing_files(model_tool, tmp_path):
    assert invoke(model_tool, tmp_path, "prepare").returncode == 0
    config = (tmp_path / "config/service.json").read_bytes()
    result = invoke(model_tool, tmp_path, "delete-model", "--model", "qwen3-asr-1.7b")
    assert result.returncode != 0
    assert records(result)[-1]["code"] == "model_in_use"
    result = invoke(model_tool, tmp_path, "select-model", "--model", "qwen3-asr-0.6b-4bit")
    assert result.returncode != 0
    assert records(result)[-1]["code"] == "invalid_assets"
    assert (tmp_path / "config/service.json").read_bytes() == config


def test_migration_preserves_partials_and_user_files_until_commit(model_tool, tmp_path):
    root, destination = tmp_path / "data", tmp_path / "external-models"
    assert invoke(model_tool, root, "prepare").returncode == 0
    original = (root / "config/service.json").read_bytes()
    token = (root / "config/api-token").read_bytes()
    lock = json.loads((ROOT / "Native/Sources/CyberAudioCore/Models.lock.json").read_text())
    asset = lock["files"][0]
    partial = root / "tmp/downloads" / (asset["sha256"] + ".partial")
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"resume")
    user_file = root / "models/keep-my-notes.txt"
    user_file.write_text("user data")
    result = invoke(model_tool, root, "move-models", "--destination", str(destination), "--transaction", "true")
    assert result.returncode == 0, result.stdout
    moved = destination / ".downloads" / partial.name
    assert moved.read_bytes() == b"resume"
    assert partial.read_bytes() == b"resume"
    assert user_file.read_text() == "user data"
    assert json.loads((root / "config/service.json").read_bytes())["models_dir"] == str(destination)
    assert invoke(model_tool, root, "rollback-model-change").returncode == 0
    assert (root / "config/service.json").read_bytes() == original
    assert partial.read_bytes() == b"resume" and not moved.exists()
    assert invoke(model_tool, root, "move-models", "--destination", str(destination), "--transaction", "true").returncode == 0
    assert invoke(model_tool, root, "commit-model-change").returncode == 0
    assert moved.read_bytes() == b"resume" and not partial.exists()
    assert user_file.read_text() == "user data"
    assert (root / "config/api-token").read_bytes() == token


def test_migration_conflict_and_crash_recovery_keep_original_config(model_tool, tmp_path):
    root, destination = tmp_path / "data", tmp_path / "target"
    assert invoke(model_tool, root, "prepare").returncode == 0
    original = (root / "config/service.json").read_bytes()
    asset = json.loads((ROOT / "Native/Sources/CyberAudioCore/Models.lock.json").read_text())["files"][0]
    name = asset["sha256"] + ".partial"
    source = root / "tmp/downloads" / name
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original")
    target = destination / ".downloads" / name
    target.parent.mkdir(parents=True)
    target.write_bytes(b"conflict")
    result = invoke(model_tool, root, "move-models", "--destination", str(destination))
    assert result.returncode != 0
    assert records(result)[-1]["code"] == "model_conflict"
    assert target.read_bytes() == b"conflict"
    assert (root / "config/service.json").read_bytes() == original
    target.unlink()
    assert invoke(model_tool, root, "move-models", "--destination", str(destination), "--transaction", "true").returncode == 0
    # Simulate relaunch after a process died before accepting the candidate engine.
    assert invoke(model_tool, root, "prepare").returncode == 0
    assert (root / "config/service.json").read_bytes() == original
    assert source.read_bytes() == b"original" and not target.exists()


def test_migration_rejects_target_symlink_ancestors(model_tool, tmp_path):
    root, destination, outside = tmp_path / "data", tmp_path / "target", tmp_path / "user-files"
    assert invoke(model_tool, root, "prepare").returncode == 0
    config = (root / "config/service.json").read_bytes()
    asset = json.loads((ROOT / "Native/Sources/CyberAudioCore/Models.lock.json").read_text())["files"][0]
    name = asset["sha256"] + ".partial"
    partial = root / "tmp/downloads" / name
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"resume")
    destination.mkdir()
    outside.mkdir()
    (outside / name).write_bytes(b"resume")
    (destination / ".downloads").symlink_to(outside, target_is_directory=True)
    result = invoke(model_tool, root, "move-models", "--destination", str(destination))
    assert result.returncode != 0
    assert records(result)[-1]["code"] == "unsafe_download_path"
    assert (root / "config/service.json").read_bytes() == config
    assert partial.read_bytes() == b"resume" and (outside / name).read_bytes() == b"resume"


def test_unavailable_source_does_not_change_selection_or_publish_files(model_tool, tmp_path):
    catalog = records(invoke(model_tool, tmp_path, "catalog"))[-1]
    model = next((model for model in catalog["models"] if "huggingface" not in model["sources"]), None)
    if model is None:
        pytest.skip("All catalog mirrors have been verified")
    assert invoke(model_tool, tmp_path, "prepare").returncode == 0
    config = (tmp_path / "config/service.json").read_bytes()
    result = invoke(model_tool, tmp_path, "download", "--model", model["id"], "--source", "huggingface")
    assert result.returncode != 0
    assert records(result)[-1]["code"] == "source_unavailable"
    assert (tmp_path / "config/service.json").read_bytes() == config
    assert not (tmp_path / "models" / model["repository"].split("/")[-1]).exists()


def test_disconnected_model_volume_is_not_recreated_or_selected(model_tool, tmp_path):
    assert invoke(model_tool, tmp_path, "prepare").returncode == 0
    directory = Path("/Volumes") / ("CyberAudio-absent-" + uuid.uuid4().hex) / "models"
    config_file = tmp_path / "config/service.json"
    config = json.loads(config_file.read_bytes())
    config["models_dir"] = str(directory)
    config["asr_dir"] = str(directory / "Qwen3-ASR-1.7B-8bit")
    config["tts_dir"] = str(directory / "Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit")
    config_file.write_text(json.dumps(config))
    original = config_file.read_bytes()
    result = invoke(model_tool, tmp_path, "models")
    assert result.returncode == 0, result.stdout
    assert {model["state"] for model in records(result)[-1]["models"]} == {"unavailable"}
    result = invoke(model_tool, tmp_path, "download", "--model", "qwen3-asr-1.7b")
    assert result.returncode != 0 and records(result)[-1]["code"] == "model_storage_unavailable"
    assert config_file.read_bytes() == original and not directory.parent.exists()


def test_one_unsafe_model_does_not_hide_storage_and_other_models(model_tool, tmp_path):
    assert invoke(model_tool, tmp_path, "prepare").returncode == 0
    catalog = records(invoke(model_tool, tmp_path, "catalog"))[-1]
    model = catalog["models"][0]
    directory = tmp_path / "models" / model["repository"].split("/")[-1]
    directory.mkdir()
    (directory / "config.json").symlink_to(tmp_path / "user-file.json")
    (tmp_path / "user-file.json").write_text("user data")
    result = invoke(model_tool, tmp_path, "models")
    assert result.returncode == 0, result.stdout
    status = records(result)[-1]
    assert len(status["models"]) == 8 and status["models_directory"] == str(tmp_path / "models")
    invalid = next(item for item in status["models"] if item["id"] == model["id"])
    assert invalid["state"] == "invalid" and invalid["problem"]
    assert (tmp_path / "user-file.json").read_text() == "user data"


def test_inactive_delete_clears_own_resume_data_and_preserves_shared_cache(model_tool, tmp_path):
    assert invoke(model_tool, tmp_path, "prepare").returncode == 0
    config_file = tmp_path / "config/service.json"
    config = json.loads(config_file.read_bytes())
    config.update(asr_model_id="qwen3-asr-0.6b-4bit", tts_model_id="qwen3-tts-0.6b-4bit",
                  asr_dir=str(tmp_path / "other-asr"), tts_dir=str(tmp_path / "other-tts"))
    config_file.write_text(json.dumps(config))
    original = config_file.read_bytes()
    catalog = json.loads((ROOT / "Native/Sources/CyberAudioCore/ModelCatalog.lock.json").read_text())["models"]
    asr = next(item for item in catalog if item["id"] == "qwen3-asr-1.7b")
    tts = next(item for item in catalog if item["id"] == "qwen3-tts-1.7b")
    unique = next(file["sha256"] for file in asr["files"] if file["path"].endswith("/model.safetensors"))
    other_tts = next(item for item in catalog if item["id"] == "qwen3-tts-0.6b-4bit")
    common = {file["sha256"] for file in other_tts["files"]}
    shared = next(file["sha256"] for file in tts["files"] if file["sha256"] in common)
    staging = tmp_path / "tmp/downloads"
    staging.mkdir(parents=True)
    own = staging / (unique + ".partial")
    common_partial = staging / (shared + ".partial")
    own.write_bytes(b"own resume data")
    common_partial.write_bytes(b"shared resume data")
    notes = staging / "user-notes.txt"
    notes.write_text("preserve")
    assert invoke(model_tool, tmp_path, "delete-model", "--model", asr["id"]).returncode == 0
    assert not own.exists()
    assert invoke(model_tool, tmp_path, "delete-model", "--model", tts["id"]).returncode == 0
    assert common_partial.read_bytes() == b"shared resume data" and notes.read_text() == "preserve"
    assert config_file.read_bytes() == original
