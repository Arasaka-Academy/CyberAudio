#!/bin/bash
# Source this file in UI build/test entry points. SwiftUI's @Entry macro needs
# the Xcode plugin, which is not included in the standalone Command Line Tools.
if [[ -z "${DEVELOPER_DIR:-}" && "$(xcode-select -p)" == */CommandLineTools && -d /Applications/Xcode.app/Contents/Developer ]]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi
