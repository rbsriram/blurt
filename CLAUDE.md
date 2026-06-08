# CLAUDE.md — operating brief for Blurt

This file is loaded automatically at the start of every Claude Code session.
Read it, then run the startup ritual below. It is the contract for how to work
on this project.

## What Blurt is

A local-first, open-source scratchpad. One append-only stream of notes. The
product's whole value is the **ghost**: as you type, a silent semantic search
surfaces an existing note you may be updating. No folders, no tags. Everything
runs locally (SQLite + Ollama embeddings); nothing leaves the machine.

Full product spec: `docs/SCRATCHPAD_PRD.md` (a sketch, not gospel — see
`docs/DECISIONS.md` for where and why we diverged).

**Active work:** `docs/UX.md` is the APPROVED, canonical interaction spec. Backend
and front end are both built and green. Read `docs/STATE.md` first for the current
status and the next actions.

## Operating mandate

The owner is not a coder and does not provide technical oversight. You are the
CTO. That means:

- Make the call. Do not ask for permission on technical decisions. Decide,
  implement, and record the decision in `docs/DECISIONS.md`.
- Brutal quality bar. Modular, reusable, minimal. No dead code, no redundancy,
  no "good enough that could be better." Comments explain *why*.
- Call out anything in the spec/tests that is wrong or unrealistic, fix it, and
  move on. The PRD and the provided test suite are guides, not scripture.
- Security is non-negotiable: localhost-only bind, escape-before-render,
  owner-only DB file, no telemetry.
- The product must feel **instant**. Saving never waits on embeddings; UI
  mutations are optimistic; interactive search/ghost are never starved by
  background work.

## Start every session here

```bash
./scripts/startup.sh
```

It prints current state, runs health checks (lint, offline tests, app builds),
and reports whether Ollama is reachable. Then read `docs/STATE.md` for the live
status and the next actions.

## End every session here

```bash
./scripts/winddown.sh
```

It lints, runs offline tests, and reminds you to update `docs/STATE.md` and
`docs/DECISIONS.md`. Do not end a session with those stale. Leave the repo so
the next session can resume in under a minute.

## How to run and test

```bash
# Run the app (your data: ./blurt.db)
./.venv/bin/python main.py            # http://localhost:7337

# Offline unit tests (fast, no Ollama)
./.venv/bin/pytest tests/test_unit.py -q
./.venv/bin/ruff check blurt main.py tests

# Full integration suite (needs Ollama running). Use a throwaway DB + test mode:
pkill -f "uvicorn blurt.app"
BLURT_TESTING=1 BLURT_DB_PATH=/tmp/blurt_test.db ./.venv/bin/python -m \
  uvicorn blurt.app:create_app --factory --host 127.0.0.1 --port 7337 --log-level warning &
sleep 4 && ./.venv/bin/pytest docs/test_suite.py -q
```

Note: `docs/test_suite.py` hardcodes port 7337 and needs `BLURT_TESTING=1`
(for `/api/test/reset`). If you run a demo server for the owner on 7337,
remember to restore it after a test run (the demo uses `./blurt.db`, the test
server uses a throwaway DB, so demo data is safe).

## Architecture in one breath

`blurt/db` (SQLite + sqlite-vec, source-of-truth embeddings + active-only vector
index) → `blurt/core` (embedder, chunker, background indexer, hybrid retriever)
→ `blurt/api` (FastAPI routes + schemas) → `blurt/static` (vanilla JS UI). Full
map: `docs/ARCHITECTURE.md`.

## Invariants you must not break

- The vector index (`vec_chunks`) holds **active** chunks only. Supersede pulls
  vectors; restore re-adds them from the durable BLOB in `chunks` (no
  re-embedding). Search is therefore free of superseded notes.
- Stored content is **verbatim** (whitespace preserved). Validation only rejects
  empty/whitespace-only/null-byte; it never mutates.
- Rendering escapes HTML *before* applying markdown. Never inject raw user HTML.
- Server binds `127.0.0.1`. `/api/test/*` exists only when `BLURT_TESTING=1`.
