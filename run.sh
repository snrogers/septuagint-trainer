#!/usr/bin/env bash
# Start the Septuagint reading trainer, setting up data on first run.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8710}"

if [ ! -f data/lxx.db ]; then
  echo "First run: building the corpus (this takes a few minutes)."
  echo
  if [ ! -d data/raw/lxxmorph ] || [ -z "$(ls -A data/raw/lxxmorph 2>/dev/null)" ]; then
    python3 scripts/fetch_data.py
  fi
  python3 scripts/build_db.py
  echo
fi

exec python3 -m lxx.app --port "$PORT" "$@"
