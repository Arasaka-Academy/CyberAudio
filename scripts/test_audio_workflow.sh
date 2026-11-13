#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
scratch="${CYBERAUDIO_NATIVE_SCRATCH:-$PWD/.context/native-build}"
native_bin="$(swift build --build-system native --package-path Native --scratch-path "$scratch" -c release --show-bin-path)"
swift build --build-system native --package-path Native --scratch-path "$scratch" -c release --skip-update --product CyberAudioFixture
fixture="$(mktemp -d "${TMPDIR:-/tmp}/CyberAudio-workflow.XXXXXX")"
trap 'rm -rf "$fixture"' EXIT
app="$fixture/AudioWorkflowTests.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Helpers" "$app/Contents/Resources"
swiftc -swift-version 5 -parse-as-library -target arm64-apple-macos26.0 \
  Sources/CyberAudio/AudioWorkspace.swift Sources/CyberAudio/AudioWaveform.swift Sources/CyberAudio/BackendController.swift Sources/CyberAudio/UpdateGate.swift tests/AudioWorkflowTests.swift \
  -o "$app/Contents/MacOS/AudioWorkflowTests"
swiftc -swift-version 5 -parse-as-library -target arm64-apple-macos26.0 \
  Sources/CyberAudio/BackendController.swift tests/BackendLifecycleTests.swift \
  -o "$app/Contents/MacOS/BackendLifecycleTests"
cp "$native_bin/CyberAudioFixture" "$app/Contents/Helpers/CyberAudioService"
cp packaging/offline.sb "$app/Contents/Resources/offline.sb"
touch "$app/Contents/Resources/mlx.metallib"
/usr/libexec/PlistBuddy -c 'Add :CFBundleExecutable string AudioWorkflowTests' \
  -c 'Add :CFBundleIdentifier string local.cyberaudio.tests.workflow' \
  -c 'Add :CFBundlePackageType string APPL' "$app/Contents/Info.plist"
codesign --force --sign - "$app/Contents/Helpers/CyberAudioService"
codesign --force --sign - "$app"
export CYBERAUDIO_TEST_FFMPEG="$(command -v ffmpeg)"
"$app/Contents/MacOS/BackendLifecycleTests" --data-dir "$fixture/backend"
"$app/Contents/MacOS/AudioWorkflowTests" --data-dir "$fixture/data"
