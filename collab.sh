#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

if command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i python3 automation/runner.py "$@"
fi

exec python3 automation/runner.py "$@"
