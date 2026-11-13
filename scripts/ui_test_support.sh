#!/bin/bash
# Shared by native UI test drivers; source from the repository root.
source scripts/ui_toolchain.sh
bash scripts/build_ui.sh
binary_dir="$(swift build --build-system native -c release --show-bin-path)"
ui_link_flags=(-I "$binary_dir/Modules" -F "$binary_dir" -framework Sparkle
  -Xlinker -rpath -Xlinker '@executable_path/../Frameworks' -Xlinker -rpath -Xlinker "$binary_dir")
for dependency in Luminare VariadicViews SwiftUIIntrospect Waveform; do
  for object in "$binary_dir/$dependency.build/"*.swift.o; do
    [[ -f "$object" ]] || { printf 'Missing UI dependency object: %s\n' "$object" >&2; exit 1; }
    ui_link_flags+=("$object")
  done
done
