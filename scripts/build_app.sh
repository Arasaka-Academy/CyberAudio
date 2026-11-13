#!/bin/bash
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_root"
python3 scripts/sign_app.py preflight
native_scratch="${CYBERAUDIO_NATIVE_SCRATCH:-$project_root/.context/native-build}"
bash scripts/build_native.sh
native_binary_dir="$(swift build --build-system native --package-path Native --scratch-path "$native_scratch" -c release --show-bin-path)"
metal_file="$(bash scripts/prepare_metal.sh)"
source scripts/ui_toolchain.sh
bash scripts/build_ui.sh
binary_dir="$(swift build --build-system native -c release --show-bin-path)"
app="$project_root/dist/CyberAudio.app"
rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources/ThirdParty" "$app/Contents/Helpers" "$app/Contents/Frameworks"
framework="$binary_dir/Sparkle.framework"
if [[ ! -d "$framework" ]]; then
  printf '%s\n' 'SwiftPM did not produce Sparkle.framework.' >&2
  exit 1
fi
ditto "$framework" "$app/Contents/Frameworks/Sparkle.framework"
cp "$binary_dir/CyberAudio" "$app/Contents/MacOS/"
cp "$native_binary_dir/CyberAudioService" "$native_binary_dir/CyberAudioAssets" "$app/Contents/Helpers/"
# SwiftPM command-line resource lookup is relative to the executable directory.
bash scripts/copy_swiftpm_bundles.sh "$native_binary_dir" "$app/Contents/Helpers"
bash scripts/copy_swiftpm_bundles.sh "$binary_dir" "$app/Contents/Resources"
cp packaging/Info.plist "$app/Contents/Info.plist"
cp packaging/offline.sb "$metal_file" "$app/Contents/Resources/"
cp design/icons/CyberAudio.icns design/icons/MenuBarIdle{,@2x}.png design/icons/MenuBarActive{,@2x}.png "$app/Contents/Resources/"
for checkout in "$project_root/.build/checkouts/"* "$native_scratch"/checkouts/*; do
  for license in "$checkout"/LICENSE "$checkout"/LICENSE.txt "$checkout"/LICENSE.md "$checkout"/NOTICE "$checkout"/COPYING; do
    if [[ -f "$license" ]]; then
      cp "$license" "$app/Contents/Resources/ThirdParty/$(basename "$checkout")-$(basename "$license")"
    fi
  done
done
for component in mlx mlx-c fmt; do
  cp "$native_scratch/checkouts/mlx-swift/Source/Cmlx/$component/LICENSE" "$app/Contents/Resources/ThirdParty/$component-LICENSE"
done
cp "$native_scratch/checkouts/swift-nio/Sources/CNIOLLHTTP/LICENSE" "$app/Contents/Resources/ThirdParty/llhttp-LICENSE"
cp Native/THIRD_PARTY.md "$app/Contents/Resources/ThirdParty/README.md"
cp packaging/UI-THIRD-PARTY.md "$app/Contents/Resources/ThirdParty/UI.md"
python3 scripts/sign_app.py sign "$app"
# Catch missing bundles and accidental reliance on a source-tree resource path.
asset_smoke="$(mktemp -d "${TMPDIR:-/tmp}/CyberAudio-assets-smoke.XXXXXX")"
trap 'rm -rf "$asset_smoke"' EXIT
"$app/Contents/Helpers/CyberAudioAssets" prepare --root "$asset_smoke"
"$app/Contents/Helpers/CyberAudioAssets" catalog --json true >/dev/null
"$app/Contents/Helpers/CyberAudioAssets" models --root "$asset_smoke" --json true >/dev/null
python_resources="$(find "$app" \( -name '*.py' -o -name '*.pyc' -o -name '.venv' -o -name 'libpython*' \) -print)"
if [[ -n "$python_resources" ]]; then
  printf '%s\n' 'Unexpected Python runtime resource in App.' >&2
  exit 1
fi
for executable in CyberAudioService CyberAudioAssets; do
  if [[ "$(lipo -archs "$app/Contents/Helpers/$executable")" != arm64 ]] || otool -L "$app/Contents/Helpers/$executable" | /usr/bin/grep -i python; then
    printf '%s\n' 'Unexpected native helper architecture or Python linkage.' >&2
    exit 1
  fi
done
python3 scripts/sign_app.py notarize "$app"
printf '%s\n' "$app"
