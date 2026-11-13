#!/usr/bin/env python3
"""Refresh immutable ModelScope snapshots; run explicitly on the build machine."""
import concurrent.futures
import json
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LEGACY = json.loads((ROOT / "Native/Sources/CyberAudioCore/Models.lock.json").read_text())


def get_json(url):
    with urllib.request.urlopen(url, timeout=45) as response:
        return json.load(response)


def snapshot(spec):
    component, size, bits = spec
    suffix = f"Qwen3-ASR-{size}-{bits}" if component == "asr" else f"Qwen3-TTS-12Hz-{size}-CustomVoice-{bits}"
    repository = "mlx-community/" + suffix
    git = subprocess.run(["git", "ls-remote", "https://modelscope.cn/" + repository + ".git", "refs/heads/master"],
                         capture_output=True, text=True, check=True, timeout=60)
    revision = git.stdout.split()[0]
    assert len(revision) == 40
    data = get_json(f"https://modelscope.cn/api/v1/models/{repository}/repo/files?Revision={revision}&Recursive=true")
    assert data["Code"] == 200, data.get("Message")
    files = []
    for file in data["Data"]["Files"]:
        path = file["Path"]
        if file.get("Type") != "blob" or not (path.endswith((".json", ".safetensors", ".txt")) or path == "README.md"):
            continue
        assert len(file["Sha256"]) == 64 and file["Size"] > 0, path
        files.append({"path": component + "/" + path, "size": file["Size"], "sha256": file["Sha256"]})
    assert any(f["path"].endswith(".safetensors") for f in files)
    default = size == "1.7B" and bits == "8bit"
    model_id = f"qwen3-{component}-{size.lower()}" + ("" if default else "-" + bits)
    sources = {"modelscope": {"revision": revision}}
    canonical = revision
    if default:
        original = [file for file in LEGACY["files"] if file["path"].startswith(component + "/")]
        pinned = {file["path"]: file for file in files}
        assert all(pinned.get(file["path"]) == file for file in original), "Default snapshot differs from production"
        files = original
        canonical = LEGACY["models"][component]["revision"]
        sources["huggingface"] = {"revision": canonical}
    else:
        try:
            hf = get_json(f"https://huggingface.co/api/models/{repository}?blobs=true")
            hf_files = {file["rfilename"]: file for file in hf["siblings"]}
            # Only advertise a second transport when every required byte is pinned identically.
            for file in files:
                relative = file["path"].split("/", 1)[1]
                item = hf_files[relative]
                if "lfs" in item:
                    assert item["lfs"]["sha256"] == file["sha256"] and item["size"] == file["size"]
                else:
                    import hashlib
                    with urllib.request.urlopen(f"https://huggingface.co/{repository}/resolve/{hf['sha']}/{relative}", timeout=30) as response:
                        content = response.read(4 * 1024 * 1024)
                    assert len(content) == file["size"] and hashlib.sha256(content).hexdigest() == file["sha256"]
            canonical = hf["sha"]
            sources["huggingface"] = {"revision": canonical}
        except Exception:
            pass  # An unverified mirror is deliberately unavailable in the catalog.
    return {"id": model_id, "component": component, "name": "Qwen3-ASR" if component == "asr" else "Qwen3-TTS CustomVoice",
            "parameters": size, "quantization": bits, "repository": repository, "revision": canonical,
            "supports_instructions": component == "tts" and size == "1.7B", "sources": sources,
            "files": sorted(files, key=lambda file: file["path"])}


if __name__ == "__main__":
    specs = [(kind, size, bits) for kind in ["asr", "tts"] for size in ["0.6B", "1.7B"] for bits in ["4bit", "8bit"]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        models = list(pool.map(snapshot, specs))
    target = ROOT / "Native/Sources/CyberAudioCore/ModelCatalog.lock.json"
    target.write_text(json.dumps({"schema": 1, "models": models}, ensure_ascii=False, indent=2) + "\n")
    for model in models:
        print(model["id"], sum(file["size"] for file in model["files"]), ",".join(model["sources"]))
