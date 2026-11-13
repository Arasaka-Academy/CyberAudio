#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
binary_dir="$(swift build --build-system native -c release --show-bin-path)"
sources=()
for source in Sources/CyberAudio/*.swift; do
  if [[ "$source" != */CyberAudioApp.swift ]]; then sources+=("$source"); fi
done
mkdir -p .build/tests
swiftc -swift-version 5 -parse-as-library -target arm64-apple-macos26.0 \
  -F "$binary_dir" -framework Sparkle -Xlinker -rpath -Xlinker "$binary_dir" \
  "${sources[@]}" tests/ModelManagementFixture.swift tests/WorkspaceLayoutTests.swift -o .build/tests/workspace-layout
.build/tests/workspace-layout "${1:-.context/compact-dictation-20261106/layout}"
