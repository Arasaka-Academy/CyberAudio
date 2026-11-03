"""Configure per-user assets. Never registers a system service or changes networking."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.home() / "Library/Application Support/CyberAudio")
    parser.add_argument("--asr-source", type=Path)
    parser.add_argument("--reuse-token", type=Path)
    parser.add_argument("--https-url", default="")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    os.umask(0o077)
    root = args.root
    for directory in [root, root / "config", root / "logs", root / "tmp", root / "models", root / "reports"]:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    asr = root / "models/Qwen3-ASR-1.7B-8bit"
    if not asr.exists() and args.asr_source:
        # APFS copy-on-write copy keeps rollback independent without duplicating initial blocks.
        subprocess.run(["/bin/cp", "-cR", str(args.asr_source), str(asr)], check=True)
    if not (asr / "model.safetensors").is_file():
        raise RuntimeError("ASR weights are missing")
    token = root / "config/api-token"
    if not token.exists():
        token.write_text(args.reuse_token.read_text() if args.reuse_token else secrets.token_urlsafe(48) + "\n")
    token.chmod(0o600)
    config = root / "config/service.json"
    if not config.exists():
        config.write_text(json.dumps({"asr_dir": str(asr),
            "tts_dir": str(root / "models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"),
            "ffmpeg": "/opt/homebrew/bin/ffmpeg", "port": args.port, "https_url": args.https_url}, indent=2) + "\n")
    (root / "config/offline.sb").write_text("(version 1)\n(allow default)\n(deny network-outbound)\n")
    print("CyberAudio configuration prepared; token is stored privately, not printed.")


if __name__ == "__main__":
    main()
