#!/bin/bash
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_root"
mkdir -p .build/tests
swiftc -swift-version 5 -parse-as-library -target arm64-apple-macos26.0 \
  Sources/CyberAudio/UpdateGate.swift tests/UpdateGateTests.swift -o .build/tests/update-gate
.build/tests/update-gate
