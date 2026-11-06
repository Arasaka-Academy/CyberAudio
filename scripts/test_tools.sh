#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'Development checks require uv. Install uv, then run make check again.' >&2
  exit 1
fi
# Preserve historical Python inference environments, including an old .venv.
export UV_PROJECT_ENVIRONMENT="$PWD/.context/tool-tests-venv"
uv run --locked --python 3.14 --group dev python -m pytest -c pyproject.toml tests/ -q
