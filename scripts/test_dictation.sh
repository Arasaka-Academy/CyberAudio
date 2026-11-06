#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .build/tests
swiftc -swift-version 5 -parse-as-library -target arm64-apple-macos26.0 \
  Sources/CyberAudio/DictationGesture.swift Sources/CyberAudio/DictationShortcut.swift tests/DictationGestureTests.swift \
  -o .build/tests/dictation-gesture
.build/tests/dictation-gesture
swiftc -swift-version 5 -parse-as-library -target arm64-apple-macos26.0 \
  Sources/CyberAudio/TextInsertionService.swift Sources/CyberAudio/UpdateGate.swift tests/TextInsertionTests.swift \
  -o .build/tests/text-insertion
.build/tests/text-insertion
swiftc -swift-version 5 -parse-as-library -target arm64-apple-macos26.0 \
  Sources/CyberAudio/AudioWorkspace.swift Sources/CyberAudio/BackendController.swift tests/AudioWorkspaceTests.swift \
  -o .build/tests/audio-workspace
.build/tests/audio-workspace
