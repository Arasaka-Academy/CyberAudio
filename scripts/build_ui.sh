#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/ui_toolchain.sh
swift package resolve --skip-update
python3 scripts/patch_waveform.py
swift build --build-system native -c release --skip-update
binary_dir="$(swift build --build-system native -c release --show-bin-path)"
shader="$binary_dir/Waveform_Waveform.bundle/Waveform.metal"
library="$binary_dir/Waveform_Waveform.bundle/default.metallib"
if [[ ! -f "$library" || "$shader" -nt "$library" ]]; then
  scratch="$(mktemp -d "${TMPDIR:-/tmp}/CyberAudio-waveform-metal.XXXXXX")"
  trap 'rm -rf "$scratch"' EXIT
  xcrun --sdk macosx metal -target air64-apple-macos11.0 -c "$shader" -o "$scratch/Waveform.air"
  xcrun --sdk macosx metallib "$scratch/Waveform.air" -o "$scratch/default.metallib"
  cp "$scratch/default.metallib" "$library"
fi
