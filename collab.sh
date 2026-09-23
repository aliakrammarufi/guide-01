#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

if [[ "${1-}" == "--queue" ]]; then
  shift
  exec python3 automation/watch.py --enqueue "$@"
fi

if [[ "${1-}" == "--watch" ]]; then
  shift
  if command -v caffeinate >/dev/null 2>&1; then
    exec caffeinate -i python3 automation/watch.py --watch "$@"
  fi
  exec python3 automation/watch.py --watch "$@"
fi

if [[ "${1-}" == "--stop-watch" ]]; then
  exec python3 automation/watch.py --stop
fi

if [[ "${1-}" == "--status" ]]; then
  exec python3 automation/watch.py --status
fi

if command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i python3 automation/runner.py "$@"
fi

exec python3 automation/runner.py "$@"
