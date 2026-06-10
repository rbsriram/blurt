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
REPO="$(pwd)"
PY="$REPO/.venv/bin/python"

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

# Build a throwaway "blurt dev.app" and launch it through LaunchServices (`open`), the
# way the shipped blurt.app starts. macOS reads the dock-hover and Cmd-Tab name from the
# launched bundle's identity, which only LaunchServices confers; exec'ing the inner binary
# directly would resolve the main bundle to the framework Python.app and show "Python"
# again. Rebuilt each run so it always points at this clone; lives under the dev data dir,
# never near your real app.
APP="$DEV_DIR/blurt dev.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>blurt dev</string>
  <key>CFBundleDisplayName</key><string>blurt dev</string>
  <key>CFBundleExecutable</key><string>blurt-dev</string>
  <key>CFBundleIdentifier</key><string>com.rbsriram.blurt.dev</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>Blurt</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

cp "$REPO/blurt/assets/Blurt.icns" "$APP/Contents/Resources/Blurt.icns" 2>/dev/null || true

# The bundle's launcher: bake in this clone's paths and the dev env, then exec.
cat > "$APP/Contents/MacOS/blurt-dev" <<LAUNCHER
#!/bin/bash
export BLURT_DB_PATH="$DEV_DIR/blurt.db"
export BLURT_PORT=7338
export BLURT_WINDOW_TITLE="blurt dev"
exec "$PY" "$REPO/scripts/dev_app.py"
LAUNCHER
chmod +x "$APP/Contents/MacOS/blurt-dev"

echo "Blurt Dev  ·  data: $DEV_DIR  ·  branch: $(git branch --show-current)"
# -n: always a fresh instance (so a relaunch picks up new code instead of just
# activating the old window). Logs go to the dev dir since `open` detaches from the shell.
exec open -n --stdout "$DEV_DIR/dev.log" --stderr "$DEV_DIR/dev.log" "$APP"
