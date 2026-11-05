#!/bin/bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_root"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  printf '%s\n' 'DMG packaging requires an Apple Silicon Mac.' >&2
  exit 1
fi

app_version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' packaging/Info.plist)"
release_version="${1:-$app_version}"
release_version="${release_version#v}"
version_pattern='^([0-9]+\.[0-9]+\.[0-9]+)(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$'
if [[ ! "$release_version" =~ $version_pattern ]]; then
  printf '%s\n' 'Use a release tag such as v0.1.0 or v0.1.0-rc.1.' >&2
  exit 1
fi
if [[ "${BASH_REMATCH[1]}" != "$app_version" ]]; then
  printf 'Release version %s does not match Info.plist version %s.\n' "$release_version" "$app_version" >&2
  exit 1
fi

if [[ "${2:-}" != "--reuse-app" ]]; then
  bash scripts/build_app.sh
fi
app="$project_root/dist/CyberAudio.app"
python3 - "$app/Contents/Info.plist" <<'PY'
import plistlib
import sys
from pathlib import Path
source = plistlib.loads(Path("packaging/Info.plist").read_bytes())
built = plistlib.loads(Path(sys.argv[1]).read_bytes())
if built != source:
    raise SystemExit("Built App Info.plist differs from release source; rebuild the App.")
PY
if [[ "$(lipo -archs "$app/Contents/MacOS/CyberAudio")" != "arm64" ]]; then
  printf '%s\n' 'Expected an arm64 application binary.' >&2
  exit 1
fi
codesign --verify --deep --strict "$app"

staging="$(mktemp -d "${TMPDIR:-/tmp}/CyberAudio-dmg.XXXXXX")"
trap 'rm -rf "$staging"' EXIT
image="$staging/image"
mkdir -p "$image/Deployment/scripts"
ditto "$app" "$image/CyberAudio.app"
ln -s /Applications "$image/Applications"
cp packaging/DMG-README.txt "$image/使用说明.txt"
cp docs/GUIDE.md "$image/Deployment/GUIDE.md"
cp requirements.lock.txt "$image/Deployment/requirements.lock.txt"
cp scripts/prepare_models.py scripts/prepare_runtime.py scripts/verify_assets.py "$image/Deployment/scripts/"

filename="CyberAudio-${release_version}-macOS-arm64.dmg"
dmg="$project_root/dist/$filename"
hdiutil create -volname "CyberAudio $release_version" -srcfolder "$image" \
  -format UDZO -ov "$dmg"
hdiutil verify "$dmg"
(
  cd "$project_root/dist"
  shasum -a 256 "$filename" > "$filename.sha256"
  shasum -a 256 -c "$filename.sha256"
)
printf '%s\n' "$dmg" "$dmg.sha256"
