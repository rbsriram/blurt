#!/usr/bin/env bash
# Open "blurt dev": a SECOND native app window running the current branch (an
# experiment), against its own data dir, alongside your real installed Blurt.
# Same as how apps ship a separate dev/canary build. Your real notes are never touched.
#
#   ./scripts/dev-app.sh           empty dev dataset
#   ./scripts/dev-app.sh --seed    start from a COPY of your real notes (test migrations)
#
# It runs on port 7338 (real app uses 7337), so both can run at once.
set -euo pipefail
cd "$(dirname "$0")/.."

DEV_DIR="${BLURT_DEV_DIR:-$HOME/.local/share/blurt-dev}"
REAL_DB="${XDG_DATA_HOME:-$HOME/.local/share}/blurt/blurt.db"
mkdir -p "$DEV_DIR"

if [ "${1:-}" = "--seed" ]; then
  if [ -f "$REAL_DB" ]; then
    cp "$REAL_DB" "$DEV_DIR/blurt.db"
    for ext in wal shm; do
      [ -f "$REAL_DB-$ext" ] && cp "$REAL_DB-$ext" "$DEV_DIR/blurt.db-$ext" || true
    done
    echo "Seeded Blurt Dev from a COPY of your real notes. The originals are untouched."
  else
    echo "No real DB at $REAL_DB; starting Blurt Dev empty."
  fi
fi

echo "Blurt Dev  ·  data: $DEV_DIR  ·  branch: $(git branch --show-current)"
exec env \
  BLURT_DB_PATH="$DEV_DIR/blurt.db" \
  BLURT_PORT=7338 \
  BLURT_WINDOW_TITLE="blurt dev" \
  ./.venv/bin/python scripts/dev_app.py
