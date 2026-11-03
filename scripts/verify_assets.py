"""Offline SHA256 verification against the recorded pinned asset manifests."""
import argparse
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, default=Path.home() / "Library/Application Support/CyberAudio")
args = parser.parse_args()
root = args.root
config = json.loads((root / "config/service.json").read_text())
report = []
for key in ["asr_dir", "tts_dir"]:
    directory = Path(config[key])
    manifest = directory / "cyberaudio-manifest.json"
    if manifest.exists():
        data = json.loads(manifest.read_text())
        files = data["files"]
        revision = data["revision"]
    else:
        data = json.loads((root / "reports/asr-provenance.json").read_text())
        files = [{"path": f["file"], "size": f["bytes"], "sha256": f["sha256"]} for f in data["files"]]
        revision = data["revision"]
    for item in files:
        path = directory / item["path"]
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024): digest.update(chunk)
        assert path.stat().st_size == item["size"] and digest.hexdigest() == item["sha256"], item["path"]
    report.append({"component": key, "revision": revision, "files_verified": len(files)})
(root / "reports/assets-verified.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
