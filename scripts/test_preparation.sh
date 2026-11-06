#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .build/tests
swiftc -swift-version 5 -parse-as-library -target arm64-apple-macos26.0 \
  Sources/CyberAudio/PreparationController.swift Sources/CyberAudio/UpdateGate.swift tests/PreparationTests.swift \
  -o .build/tests/preparation
.build/tests/preparation
