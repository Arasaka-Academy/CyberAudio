#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .build/tests
swiftc -swift-version 5 -parse-as-library -target arm64-apple-macos26.0 \
  Sources/CyberAudio/DictationGesture.swift Sources/CyberAudio/DictationShortcut.swift tests/DictationGestureTests.swift \
  -o .build/tests/dictation-gesture
.build/tests/dictation-gesture
swiftc -swift-version 5 -parse-as-library -target arm64-apple-macos26.0 \
  Sources/CyberAudio/DictationShortcut.swift Sources/CyberAudio/ShortcutRecorder.swift tests/ShortcutRecorderTests.swift \
  -o .build/tests/shortcut-recorder
.build/tests/shortcut-recorder
swiftc -swift-version 5 -parse-as-library -target arm64-apple-macos26.0 \
  Sources/CyberAudio/{DictationController,DictationGesture,DictationShortcut,DictationOverlay,ShortcutMonitor,TextInsertionService,AudioWorkspace,AudioWaveform,BackendController,StudioStyle}.swift \
  tests/DictationOverlayTests.swift -o .build/tests/dictation-overlay
.build/tests/dictation-overlay
swiftc -swift-version 5 -parse-as-library -target arm64-apple-macos26.0 \
  Sources/CyberAudio/TextInsertionService.swift Sources/CyberAudio/UpdateGate.swift tests/TextInsertionTests.swift \
  -o .build/tests/text-insertion
.build/tests/text-insertion
swiftc -swift-version 5 -parse-as-library -target arm64-apple-macos26.0 \
  Sources/CyberAudio/AudioWorkspace.swift Sources/CyberAudio/AudioWaveform.swift Sources/CyberAudio/BackendController.swift tests/AudioWorkspaceTests.swift \
  -o .build/tests/audio-workspace
.build/tests/audio-workspace
swiftc -swift-version 5 -parse-as-library -target arm64-apple-macos26.0 \
  Sources/CyberAudio/BackendController.swift tests/SpeechRequestStatusTests.swift \
  -o .build/tests/speech-request-status
.build/tests/speech-request-status
