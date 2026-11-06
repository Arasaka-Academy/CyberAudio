"""Assemble an isolated current-source App without replacing frozen artifacts."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys

base = Path(__file__).resolve().parent
project = base.parents[2]
label = sys.argv[1] if len(sys.argv) == 2 else "final-bytes-stream-failure"
assert label == "final-bytes-stream-failure"
destination = base / label
assert not destination.exists(), "Preserve prior candidate and receipts"
source = project / "dist/CyberAudio.app"
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
frozen_paths = [source / "Contents/MacOS/CyberAudio", source / "Contents/Helpers/CyberAudioService",
                source / "Contents/Helpers/CyberAudioAssets", source / "Contents/Resources/mlx.metallib",
                source / "Contents/Info.plist", project / "dist/CyberAudio-0.4.3-robustness.playback-macOS-arm64.dmg"]
frozen = {str(path.relative_to(project)): sha(path) for path in frozen_paths}
assert frozen["dist/CyberAudio.app/Contents/Helpers/CyberAudioService"] == "cc556b65e263e16218db4315ed8585dccaede7b5f539be7bbd68a4b04e841a89"
assert frozen["dist/CyberAudio-0.4.3-robustness.playback-macOS-arm64.dmg"] == "de4589039a1c42922e5cb12909804fdae1faad4cb3ddf88dfbf1bbc3c36699d7"
destination.mkdir()
app = destination / "CyberAudio.app"
subprocess.run(["/usr/bin/ditto", str(source), str(app)], check=True, timeout=180)
native = base / "native-build/arm64-apple-macosx/release"
inputs = {"Contents/MacOS/CyberAudio": project / ".build/arm64-apple-macosx/release/CyberAudio",
          "Contents/Helpers/CyberAudioService": native / "CyberAudioService",
          "Contents/Helpers/CyberAudioAssets": native / "CyberAudioAssets"}
for relative, path in inputs.items():
    shutil.copy2(path, app / relative)
lock = native / "CyberAudioNative_CyberAudioCore.bundle/Models.lock.json"
bundled_lock = app / "Contents/Helpers/CyberAudioNative_CyberAudioCore.bundle/Contents/Resources/Models.lock.json"
assert sha(lock) == sha(bundled_lock) == sha(project / "Native/Sources/CyberAudioCore/Models.lock.json")
info_path = app / "Contents/Info.plist"
info = plistlib.loads(info_path.read_bytes())
info["CFBundleIdentifier"] = "local.cyberaudio.validation." + label.replace("-", "")
info_path.write_bytes(plistlib.dumps(info))
environment = dict(os.environ)
for key in ["CYBERAUDIO_SIGN_IDENTITY", "CYBERAUDIO_TEAM_ID", "CYBERAUDIO_NOTARY_PROFILE", "CYBERAUDIO_SIGN_KEYCHAIN"]:
    environment.pop(key, None)
environment["CYBERAUDIO_SIGN_MODE"] = "adhoc"
subprocess.run(["python3", "scripts/sign_app.py", "sign", str(app)], cwd=project, env=environment, check=True, timeout=180)
subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True, timeout=120)
smoke = destination / "assets-smoke"
subprocess.run([str(app / "Contents/Helpers/CyberAudioAssets"), "prepare", "--root", str(smoke), "--json", "true"],
               check=True, capture_output=True, text=True, timeout=45)
driver = destination / "verify-native"
subprocess.run(["/usr/bin/swiftc", "-parse-as-library", "-swift-version", "5", "-target", "arm64-apple-macos26.0",
                "scripts/verify_native.swift", "-o", str(driver)], cwd=project, check=True, timeout=180)
assert frozen == {str(path.relative_to(project)): sha(path) for path in frozen_paths}
payload = {relative: {"bytes": (app / relative).stat().st_size, "sha256": sha(app / relative)} for relative in inputs}
for relative in ["Contents/Resources/mlx.metallib", "Contents/Resources/offline.sb", "Contents/Info.plist",
                 str(bundled_lock.relative_to(app))]:
    payload[relative] = {"bytes": (app / relative).stat().st_size, "sha256": sha(app / relative)}
source_files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "Sources", "Native", "scripts", "tests", "packaging", ".github", "Package.swift", "Package.resolved"],
                              cwd=project, capture_output=True, text=True, check=True).stdout.splitlines()
source_hashes = {relative: sha(project / relative) for relative in source_files if (project / relative).is_file()}
receipt = {"status": "built", "at": datetime.now(timezone.utc).isoformat(), "source_commit": subprocess.run(
    ["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True, check=True).stdout.strip(),
    "scope": "Isolated current App, Service and Assets; frozen dist App and DMG preserved; no installation/publication/notarization",
    "bundle_id": info["CFBundleIdentifier"], "signing": "adhoc", "strict_nested_signature": True,
    "bundled_assets_prepare": True, "app": str(app), "files": payload,
    "input_binaries": {relative: sha(path) for relative, path in inputs.items()},
    "driver_sha256": sha(driver), "driver_source_sha256": sha(project / "scripts/verify_native.swift"),
    "model_lock_sha256": sha(lock), "frozen_preserved": True, "frozen_sha256": frozen,
    "source_sha256": source_hashes}
(destination / "build.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps({key: receipt[key] for key in ["status", "source_commit", "bundle_id", "files", "driver_sha256", "frozen_preserved"]}, indent=2))
