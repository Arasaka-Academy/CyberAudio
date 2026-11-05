"""Stage existing Qwen assets for native validation without changing production.

Development-only Python. The Swift probe loads the staged files offline.
macOS APFS clone copies keep model weights independent without a second download.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

IDENTITIES = {
    "asr": ("mlx-community/Qwen3-ASR-1.7B-8bit", "a8379a2e2f9e313c9292cdf1af4055ab56d50d55"),
    "tts": ("mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit", "41d3337e8b7f2843a75841595fc14e4b9a7a4b96"),
}


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def prepare(service_root, destination):
    config = json.loads((service_root / "config/service.json").read_text())
    destination = destination.resolve()
    sources = {kind: Path(config[kind + "_dir"]).resolve() for kind in IDENTITIES}
    if any(destination == source or destination.is_relative_to(source) or source.is_relative_to(destination)
           for source in sources.values()):
        raise ValueError("Staging must be separate from production model directories")
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": 1, "models": {}, "files": []}
    for kind, (repository, revision) in IDENTITIES.items():
        source = sources[kind]
        model_config = json.loads((source / "config.json").read_text())
        if (model_config.get("model_type") != "qwen3_" + kind
                or model_config.get("quantization", {}).get("bits") != 8):
            raise ValueError("Unexpected model identity: " + kind)
        if kind == "tts" and (model_config.get("tts_model_type") != "custom_voice"
                              or model_config.get("tts_model_size") != "1b7"):
            raise ValueError("Expected 1.7B CustomVoice")
        target = destination / kind
        if target.is_symlink() or target.resolve() != target:
            raise ValueError("Staging directory must not be a symlink")
        if not target.exists():
            subprocess.run(["/bin/cp", "-cR", str(source), str(target)], check=True)
        expected = {}
        saved_manifest = source / "cyberaudio-manifest.json"
        if saved_manifest.exists():
            saved = json.loads(saved_manifest.read_text())
            if saved.get("repository") != repository or saved.get("revision") != revision:
                raise ValueError("Production manifest identity mismatch: " + kind)
            expected = {row["path"]: row for row in saved["files"]}
        metadata = source / ".cache/huggingface/download/model.safetensors.metadata"
        if not expected and metadata.exists():
            lines = metadata.read_text().splitlines()
            if len(lines) >= 2 and lines[0] == revision:
                expected["model.safetensors"] = {"sha256": lines[1].strip('"')}
        if "model.safetensors" not in expected:
            raise ValueError("No pinned provenance for production weights: " + kind)
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if not path.is_file() or any(part.startswith(".") for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError("Expected independent model files")
            source_hash = digest(path)
            locked = expected.get(str(relative))
            if locked and source_hash != locked["sha256"]:
                raise ValueError("Pinned source checksum mismatch: " + str(relative))
            copied = target / relative
            if (not copied.is_file() or copied.is_symlink() or copied.samefile(path)
                    or copied.stat().st_size != path.stat().st_size or digest(copied) != source_hash):
                raise ValueError("Staged file mismatch: " + str(relative))
            manifest["files"].append({"path": kind + "/" + str(relative), "size": path.stat().st_size,
                                      "sha256": source_hash})
        manifest["models"][kind] = {"repository": repository, "revision": revision,
                                    "weights_match_production": True, "pinned_weights_verified": True}
        print("Verified independent " + kind + " weights and metadata", flush=True)
    output = destination / "assets.lock.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print("Wrote " + str(output), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.service_root, args.destination)
