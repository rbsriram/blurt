#!/usr/bin/env bash
# Blurt installer. Puts a `blurt` command on your PATH. No compiler, no build:
# it only downloads Blurt's source (pinned to a release) and writes a launcher.
# Read this file before you run it. That is the whole point of keeping it short.
set -euo pipefail

VERSION="${BLURT_VERSION:-v1.0.3}"
REPO="https://github.com/rbsriram/blurt"
DEST="${BLURT_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/blurt}"
BIN="${BLURT_BIN:-$HOME/.local/bin}"

say() { printf "\033[1m%s\033[0m\n" "$*"; }

say "Installing Blurt ${VERSION} ..."
mkdir -p "$DEST/src" "$BIN"

# Fetch the pinned release source over HTTPS. Just files, nothing is executed here.
curl -fsSL "${REPO}/archive/refs/tags/${VERSION}.tar.gz" | tar -xz -C "$DEST/src" --strip-components=1

# A tiny wrapper that points the launcher at this source and runs it.
cat > "$BIN/blurt" <<EOF
#!/bin/bash
export BLURT_SRC="$DEST/src"
exec "$DEST/src/scripts/blurt" "\$@"
EOF
chmod +x "$BIN/blurt"

say "Done. The 'blurt' command is in ${BIN}."
if ! command -v ollama >/dev/null 2>&1; then
  say "Heads up: Blurt needs Ollama for its semantic search -> https://ollama.com/download"
fi
case ":$PATH:" in
  *":$BIN:"*) say "Start it:  blurt" ;;
  *) say "Add ${BIN} to your PATH, then run 'blurt':"
     say "  echo 'export PATH=\"${BIN}:\$PATH\"' >> ~/.zshrc && exec zsh" ;;
esac
