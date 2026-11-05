#!/bin/bash
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_root"
swift build -c release
binary_dir="$(swift build -c release --show-bin-path)"
app="$project_root/dist/CyberAudio.app"
rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources/backend"
mkdir -p "$app/Contents/Frameworks"
framework="$binary_dir/Sparkle.framework"
if [[ ! -d "$framework" ]]; then
  printf '%s\n' 'SwiftPM did not produce Sparkle.framework.' >&2
  exit 1
fi
ditto "$framework" "$app/Contents/Frameworks/Sparkle.framework"
cp "$binary_dir/CyberAudio" "$app/Contents/MacOS/CyberAudio"
cp packaging/Info.plist "$app/Contents/Info.plist"
cp backend/server.py "$app/Contents/Resources/backend/server.py"
cp "$project_root/.build/checkouts/Sparkle/LICENSE" "$app/Contents/Resources/Sparkle-LICENSE"
codesign --force --sign - "$app"
codesign --verify --deep --strict "$app"
printf '%s\n' "$app"
