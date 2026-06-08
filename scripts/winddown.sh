#!/usr/bin/env bash
# Session winddown ritual. Run before ending a Claude Code session.
# Verifies the repo is in a clean, resumable state and nudges doc updates.
set -uo pipefail
cd "$(dirname "$0")/.."

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }

bold "Blurt — session winddown"
echo

bold "1. Quality gate"
FAIL=0
if [ -x ./.venv/bin/ruff ]; then
  ./.venv/bin/ruff check blurt main.py tests >/dev/null 2>&1 && ok "lint clean" || { bad "lint errors"; FAIL=1; }
fi
if [ -x ./.venv/bin/pytest ]; then
  ./.venv/bin/pytest tests/test_unit.py -q >/dev/null 2>&1 && ok "offline tests pass" || { bad "unit tests failing"; FAIL=1; }
fi
./.venv/bin/python -c "from blurt.app import create_app; create_app()" >/dev/null 2>&1 && ok "app builds" || { bad "app does not build"; FAIL=1; }
echo

bold "2. Docs freshness (update these before you stop)"
warn "Is docs/STATE.md current? (what's done, what's next, known issues)"
warn "Any new decisions/divergences recorded in docs/DECISIONS.md?"
echo

bold "3. Uncommitted work"
if git rev-parse --git-dir >/dev/null 2>&1; then
  n=$(git status --porcelain | wc -l | tr -d ' ')
  echo "  $n changed file(s):"
  git --no-pager status --short | sed 's/^/    /'
  echo
  echo "  To version this session's work:"
  echo "    git add -A && git commit -m \"<what changed>\""
else
  warn "not a git repo — consider: git init && git add -A && git commit"
fi
echo

if [ "$FAIL" -eq 0 ]; then
  bold "Clean. Safe to stop."
else
  bold "NOT clean — fix the red items above before stopping, or note them in docs/STATE.md."
fi
