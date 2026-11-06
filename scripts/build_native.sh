#!/bin/bash
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_root"
swift_version="$(swift --version | sed -nE 's/.*Swift version ([0-9]+)\.([0-9]+).*/\1 \2/p' | head -1)"
read -r swift_major swift_minor <<< "$swift_version"
if [[ "${swift_major:-0}" -lt 6 || ( "$swift_major" -eq 6 && "${swift_minor:-0}" -lt 3 ) ]]; then
  printf '%s\n' 'Native Qwen requires Swift 6.3 or newer. Select a compatible toolchain before building.' >&2
  exit 1
fi
native_scratch="${CYBERAUDIO_NATIVE_SCRATCH:-$project_root/.context/native-build}"
native_cache="${CYBERAUDIO_BUILD_CACHE:-$project_root/.context/local-cache}"
native_options=(--build-system native --package-path Native --scratch-path "$native_scratch" --cache-path "$native_cache")
swift package "${native_options[@]}" resolve --force-resolved-versions
swift scripts/patch_mlx_audio.swift "$native_scratch/checkouts/mlx-audio-swift"
if [[ "${1:-}" == "test" ]]; then
  swift run "${native_options[@]}" -c release --skip-update -j "${CYBERAUDIO_BUILD_JOBS:-6}" CyberAudioCoreTests
  # Keep the opt-in real-model acceptance driver compiling in model-free CI.
  swift build "${native_options[@]}" -c release --skip-update -j "${CYBERAUDIO_BUILD_JOBS:-6}" --product CyberAudioInferenceTests
  swift build "${native_options[@]}" -c release --skip-update -j "${CYBERAUDIO_BUILD_JOBS:-6}" --product CyberAudioAssets
else
  swift build "${native_options[@]}" -c release --skip-update -j "${CYBERAUDIO_BUILD_JOBS:-6}" --product CyberAudioService
  swift build "${native_options[@]}" -c release --skip-update -j "${CYBERAUDIO_BUILD_JOBS:-6}" --product CyberAudioAssets
fi
