#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
scratch="${CYBERAUDIO_NATIVE_SCRATCH:-$PWD/.context/native-build}"
native_bin="$(swift build --build-system native --package-path Native --scratch-path "$scratch" -c release --show-bin-path)"
swift build --build-system native --package-path Native --scratch-path "$scratch" -c release --skip-update --product CyberAudioFixture
source scripts/ui_test_support.sh
fixture="$(mktemp -d "${TMPDIR:-/tmp}/CyberAudio-models.XXXXXX")"
trap 'rm -rf "$fixture"' EXIT
app="$fixture/ModelManagerTests.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Helpers" "$app/Contents/Resources"
sources=()
for source in Sources/CyberAudio/*.swift; do
  if [[ "$source" != */CyberAudioApp.swift ]]; then sources+=("$source"); fi
done
swiftc -swift-version 5 -parse-as-library -target arm64-apple-macos26.0 \
  "${ui_link_flags[@]}" \
  "${sources[@]}" tests/ModelManagementFixture.swift tests/ModelManagerTests.swift -o "$app/Contents/MacOS/ModelManagerTests"
cp "$native_bin/CyberAudioFixture" "$app/Contents/Helpers/CyberAudioService"
cp packaging/offline.sb "$app/Contents/Resources/offline.sb"
touch "$app/Contents/Resources/mlx.metallib"
/usr/libexec/PlistBuddy -c 'Add :CFBundleExecutable string ModelManagerTests' \
  -c 'Add :CFBundleIdentifier string local.cyberaudio.tests.models' \
  -c 'Add :CFBundlePackageType string APPL' "$app/Contents/Info.plist"
codesign --force --sign - "$app/Contents/Helpers/CyberAudioService"
codesign --force --sign - "$app"
"$app/Contents/MacOS/ModelManagerTests" --data-dir "$fixture/data"
