"""Fetch pinned official Sparkle tools; never fetch an unsigned moving 'latest'."""
import hashlib
from pathlib import Path
import subprocess
import tempfile

VERSION = "2.10.1"
ASSET_ID = "673946481"
SHA256 = "1a856d2a9edb8e628de88121d7be04cf0e2167f8641650b78fca00b14244cf27"
ROOT = Path(__file__).resolve().parents[1]


def fetch_tools():
    cache = ROOT / ".context/sparkle"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"Sparkle-{VERSION}.tar.xz"
    if not archive.exists():
        with tempfile.TemporaryDirectory(dir=cache) as directory:
            download = Path(directory) / "download"
            with download.open("wb") as output:
                subprocess.run(["gh", "api", "-H", "Accept: application/octet-stream",
                    f"repos/sparkle-project/Sparkle/releases/assets/{ASSET_ID}"], stdout=output, check=True)
            if hashlib.sha256(download.read_bytes()).hexdigest() != SHA256:
                raise ValueError("Official Sparkle tools archive checksum mismatch")
            download.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise ValueError("Cached Sparkle tools archive checksum mismatch")
    destination = cache / "distribution"
    if not (destination / "bin/sign_update").exists():
        destination.mkdir(exist_ok=True)
        subprocess.run(["tar", "-xJf", str(archive), "-C", str(destination)], check=True)
    return destination / "bin"


if __name__ == "__main__":
    print(fetch_tools())
