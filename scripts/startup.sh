#!/usr/bin/env bash
# Session startup ritual. Run at the start of every Claude Code session.
# Prints current state, runs health checks, reports environment readiness.
set -uo pipefail
cd "$(dirname "$0")/.."

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }

PY=./.venv/bin/python
[ -x "$PY" ] || { warn "no .venv yet — run ./setup.sh"; PY=python3; }

bold "Blurt — session startup"
echo

bold "1. Git"
if git rev-parse --git-dir >/dev/null 2>&1; then
  echo "  branch: $(git branch --show-current 2>/dev/null || echo '(detached)')"
  changes=$(git status --porcelain | wc -l | tr -d ' ')
  echo "  uncommitted changes: $changes file(s)"
  echo "  last commits:"
  git --no-pager log --oneline -3 2>/dev/null | sed 's/^/    /' || echo "    (no commits yet)"
else
  warn "not a git repo yet (git init when ready to version)"
fi
echo

bold "2. Health checks"
if [ -x ./.venv/bin/ruff ]; then
  ./.venv/bin/ruff check blurt main.py tests >/dev/null 2>&1 && ok "lint clean" || bad "lint errors (run: ruff check blurt main.py tests)"
else
  warn "ruff not installed (pip install -r requirements-dev.txt)"
fi
if [ -x ./.venv/bin/pytest ]; then
  ./.venv/bin/pytest tests/test_unit.py -q >/dev/null 2>&1 && ok "offline unit tests pass" || bad "unit tests FAILING (run: pytest tests/test_unit.py)"
else
  warn "pytest not installed (pip install -r requirements-dev.txt)"
fi
"$PY" -c "from blurt.app import create_app; create_app()" >/dev/null 2>&1 && ok "app builds" || bad "app does not build"
echo

bold "3. Environment"
if curl -s -m 2 http://localhost:11434/api/version >/dev/null 2>&1; then
  ok "Ollama reachable"
  if curl -s -m 3 http://localhost:11434/api/tags 2>/dev/null | grep -q nomic-embed-text; then
    ok "nomic-embed-text present"
  else
    warn "nomic-embed-text missing (ollama pull nomic-embed-text)"
  fi
else
  warn "Ollama not running (ghost + semantic search will be down)"
fi
if curl -s -m 2 http://localhost:7337/api/status >/dev/null 2>&1; then
  ok "a Blurt server is already running on :7337"
fi
echo

bold "4. Where things stand"
echo "  Read docs/STATE.md for live status and next actions:"
sed -n '1,40p' docs/STATE.md 2>/dev/null | sed 's/^/    /' || warn "docs/STATE.md missing"
