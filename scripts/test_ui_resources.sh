#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/ui_toolchain.sh
binary_dir="$(swift build --build-system native -c release --show-bin-path)"
fixture="$(mktemp -d "${TMPDIR:-/tmp}/CyberAudio-ui-resources.XXXXXX")"
source_bundle="$binary_dir/Waveform_Waveform.bundle"
cleanup() {
  if [[ -d "$fixture/build-tree-resource.bundle" ]]; then mv "$fixture/build-tree-resource.bundle" "$source_bundle"; fi
  rm -rf "$fixture"
}
trap cleanup EXIT
app="$fixture/WaveformProbe.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources" "$app/Contents/Frameworks"
cp .build/tests/workspace-layout "$app/Contents/MacOS/WaveformProbe"
ditto "$binary_dir/Sparkle.framework" "$app/Contents/Frameworks/Sparkle.framework"
bash scripts/copy_swiftpm_bundles.sh "$binary_dir" "$app/Contents/Resources"
/usr/libexec/PlistBuddy -c 'Add :CFBundleExecutable string WaveformProbe' \
  -c 'Add :CFBundleIdentifier string local.cyberaudio.tests.waveform-resources' \
  -c 'Add :CFBundlePackageType string APPL' "$app/Contents/Info.plist"
codesign --force --sign - "$app/Contents/Resources/Waveform_Waveform.bundle"
codesign --force --sign - "$app"
codesign --verify --deep --strict "$app"
# A packaged view must never find the absolute SwiftPM build-tree fallback.
mv "$source_bundle" "$fixture/build-tree-resource.bundle"
"$app/Contents/MacOS/WaveformProbe" "${1:-.context/design-refresh/layout}" --resource-smoke
