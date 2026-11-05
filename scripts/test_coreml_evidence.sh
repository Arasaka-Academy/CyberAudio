#!/bin/bash
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
swiftc "$project_root/Experiments/CoreML/Sources/CoreMLEvidence/Evidence.swift" \
  "$project_root/Experiments/CoreML/Tests/CoreMLEvidenceTests/EvidenceTests.swift" \
  -o "$temporary/checks"
"$temporary/checks"
