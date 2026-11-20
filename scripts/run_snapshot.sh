#!/usr/bin/env bash
# Runs the daily price snapshot. Safe to schedule hourly: products already
# checked today are skipped, so only the first successful run of the day calls the APIs.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
PY=python
command -v "$PY" >/dev/null 2>&1 || PY=python3
mkdir -p instance
{ echo "--- $(date '+%Y-%m-%d %H:%M:%S')"; "$PY" -m flask --app app snapshot "$@"; } >> instance/snapshot.log 2>&1
