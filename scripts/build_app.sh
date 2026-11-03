#!/bin/bash
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_root"
swift build -c release
binary_dir="$(swift build -c release --show-bin-path)"
app="$project_root/dist/CyberAudio.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources/backend"
cp "$binary_dir/CyberAudio" "$app/Contents/MacOS/CyberAudio"
cp packaging/Info.plist "$app/Contents/Info.plist"
cp backend/server.py "$app/Contents/Resources/backend/server.py"
codesign --force --sign - "$app"
codesign --verify --deep --strict "$app"
printf '%s\n' "$app"
