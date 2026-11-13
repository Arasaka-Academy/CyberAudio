#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/ui_test_support.sh
sources=()
for source in Sources/CyberAudio/*.swift; do
  if [[ "$source" != */CyberAudioApp.swift ]]; then sources+=("$source"); fi
done
mkdir -p .build/tests
output="${1:-${CYBERAUDIO_LAYOUT_OUTPUT:-.context/workspace-layout}}"
swiftc -swift-version 5 -parse-as-library -target arm64-apple-macos26.0 \
  "${ui_link_flags[@]}" \
  "${sources[@]}" tests/ModelManagementFixture.swift tests/WorkspaceLayoutTests.swift -o .build/tests/workspace-layout
.build/tests/workspace-layout "$output"
bash scripts/test_ui_resources.sh "$output"
