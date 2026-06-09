#!/usr/bin/env bash
# Run the CURRENT branch (an experiment) against an ISOLATED data dir, so you can
# test it without touching your real notes in ~/.local/share/blurt.
#
#   ./scripts/lab.sh           start with an empty lab dataset
#   ./scripts/lab.sh --seed    start from a COPY of your real notes (test migrations)
#
# It serves on port 7338 (not the real app's 7337), so you can run both at once.
# Open http://localhost:7338 . Your real data is never written to.
set -euo pipefail
cd "$(dirname "$0")/.."

LAB_DIR="${BLURT_LAB_DIR:-$HOME/.local/share/blurt-lab}"
REAL_DB="${XDG_DATA_HOME:-$HOME/.local/share}/blurt/blurt.db"
mkdir -p "$LAB_DIR"

if [ "${1:-}" = "--seed" ]; then
  if [ -f "$REAL_DB" ]; then
    cp "$REAL_DB" "$LAB_DIR/blurt.db"
    for ext in wal shm; do
      [ -f "$REAL_DB-$ext" ] && cp "$REAL_DB-$ext" "$LAB_DIR/blurt.db-$ext" || true
    done
    echo "Seeded the lab from a COPY of your real notes. The originals are untouched."
  else
    echo "No real DB found at $REAL_DB; starting the lab empty."
  fi
fi

echo "Lab data:    $LAB_DIR  (separate from your real notes)"
echo "Open:        http://localhost:7338"
echo "Branch:      $(git branch --show-current)"
echo
exec env BLURT_DB_PATH="$LAB_DIR/blurt.db" BLURT_PORT=7338 ./.venv/bin/python main.py
