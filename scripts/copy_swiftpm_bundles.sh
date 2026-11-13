#!/bin/bash
set -euo pipefail
source_dir="$1"
destination="$2"
mkdir -p "$destination"
for bundle in "$source_dir"/*.bundle; do
  [[ -d "$bundle" ]] || continue
  bundle_name="$(basename "$bundle")"
  bundled="$destination/$bundle_name"
  ditto "$bundle" "$bundled"
  # Native SwiftPM emits flat resource directories without bundle metadata.
  # Convert them to signed macOS resource bundles before nesting in the app.
  if [[ ! -f "$bundled/Contents/Info.plist" && ! -f "$bundled/Info.plist" ]]; then
    mkdir -p "$bundled/Contents/Resources"
    for resource in "$bundled"/*; do
      [[ "$resource" == "$bundled/Contents" ]] || mv "$resource" "$bundled/Contents/Resources/"
    done
    bundle_id="${bundle_name%.bundle}"
    /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string local.cyberaudio.resources.${bundle_id//_/-}" \
      -c 'Add :CFBundlePackageType string BNDL' "$bundled/Contents/Info.plist"
  fi
done
