#!/bin/bash
# Extract only the official matching shader. No Python interpreter or packages are installed.
set -euo pipefail
project_root="$(cd "$(dirname "$0")/.." && pwd)"
metal_directory="${CYBERAUDIO_METAL_CACHE:-$project_root/.context/native-metal}"
mkdir -p "$metal_directory"
metal_file="$metal_directory/mlx.metallib"
metal_sha='c6d7a3facb83568f6e4cb2cf4a8c736ee29302571a48343cb9a3a4d1cd3e3893'
matches() { [[ -f "$1" && "$(shasum -a 256 "$1" | cut -d ' ' -f 1)" == "$2" ]]; }
if ! matches "$metal_file" "$metal_sha"; then
  # Reuse the verified research artifact when it is available on this checkout.
  prior_metal="$project_root/.context/qwen-swift/mlx-0.32.4.metallib"
  if matches "$prior_metal" "$metal_sha"; then
    cp "$prior_metal" "$metal_file"
  else
    metal_wheel="$metal_directory/mlx_metal-0.32.4.whl"
    wheel_sha='514df92d41a286895d2564b4b18f4da45096bd7a4a0c47e1042f345ad5a7f936'
    if ! matches "$metal_wheel" "$wheel_sha"; then
      curl --fail --location --retry 3 --connect-timeout 30 \
        'https://files.pythonhosted.org/packages/2a/d6/4340ed6e184f71693d7f531c9ae55d677451351245760d71f8dd714dcbe5/mlx_metal-0.32.4-py3-none-macosx_26_0_arm64.whl' \
        --output "$metal_wheel.download"
      matches "$metal_wheel.download" "$wheel_sha" || { printf '%s\n' 'Metal archive checksum mismatch.' >&2; exit 1; }
      mv "$metal_wheel.download" "$metal_wheel"
    fi
    unzip -p "$metal_wheel" mlx/lib/mlx.metallib > "$metal_file.download"
    matches "$metal_file.download" "$metal_sha" || { printf '%s\n' 'Metal shader checksum mismatch.' >&2; exit 1; }
    mv "$metal_file.download" "$metal_file"
  fi
fi
printf '%s\n' "$metal_file"
