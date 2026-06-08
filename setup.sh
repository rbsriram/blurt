#!/usr/bin/env bash
# Blurt one-command setup. Idempotent: safe to re-run.
set -euo pipefail

cd "$(dirname "$0")"

say()  { printf "\033[1m%s\033[0m\n" "$*"; }
warn() { printf "\033[33m%s\033[0m\n" "$*"; }

# 1. Python 3.11+
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$("$c" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    major=${v%.*}; minor=${v#*.}
    if [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; then PY="$c"; break; fi
  fi
done
if [ -z "$PY" ]; then
  warn "Python 3.11+ is required. Install it from https://www.python.org/downloads/ and re-run."
  exit 1
fi
say "Using $($PY --version)"

# 2. virtualenv
if [ ! -d .venv ]; then
  say "Creating virtualenv (.venv)"
  "$PY" -m venv .venv
fi

# 3. dependencies
say "Installing dependencies"
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -r requirements.txt

# 4. Ollama (embeddings)
if command -v ollama >/dev/null 2>&1; then
  say "Pulling embedding model (nomic-embed-text)"
  ollama pull nomic-embed-text >/dev/null 2>&1 || warn "Could not pull nomic-embed-text. Pull it manually: ollama pull nomic-embed-text"
else
  warn "Ollama not found. Blurt needs it for embeddings."
  warn "Install from https://ollama.com/download, then run: ollama pull nomic-embed-text"
fi

# 5. done
say ""
say "Done. Start Blurt with:"
say "    ./.venv/bin/python main.py"
say "Then open http://localhost:7337"
