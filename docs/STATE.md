# STATE — project status

_A living snapshot of where Blurt is. Update it as things change._

## Where it is

Blurt is **feature-complete for v1 and green**. Backend and front end are both
built to `docs/UX.md`, tested, and linted.

- **Backend:** full API, hybrid search (semantic + exact), active-only vector
  index, incremental background indexing, model kept warm, localhost bind, DB file
  locked to `chmod 600`, and an always-current `scratchpad.md` mirror.
- **Front end:** bottom-pinned capture, the keyboard-browsable peek, in-place note
  editing, arrow-navigable search, checklists, list auto-continuation, a `/`
  formatting menu, auto-linked URLs, Markdown export, and a `?` cheatsheet.
- **Tests:** offline unit suite plus a full integration suite (the latter needs a
  live Ollama). Lint clean. CI runs lint + import smoke + unit tests.

## How it is built (one breath)

`blurt/db` (SQLite + sqlite-vec) -> `blurt/core` (embedder, chunker, background
indexer, hybrid retriever, Markdown mirror) -> `blurt/api` (FastAPI routes +
schemas) -> `blurt/static` (vanilla JS UI). Full map in `docs/ARCHITECTURE.md`;
the reasoning behind every notable choice is logged in `docs/DECISIONS.md`.

## Run and test

```bash
./scripts/startup.sh                       # state + health checks + env
./.venv/bin/python main.py                 # http://localhost:7337
./.venv/bin/pytest tests/test_unit.py -q   # fast, offline
./.venv/bin/ruff check blurt main.py tests # lint
```

Full integration suite (needs Ollama running, uses a throwaway DB):

```bash
BLURT_TESTING=1 BLURT_DB_PATH=/tmp/blurt_test.db ./.venv/bin/python -m \
  uvicorn blurt.app:create_app --factory --host 127.0.0.1 --port 7337 &
sleep 4 && ./.venv/bin/pytest docs/test_suite.py -q
```

## Known limitations (by design / hardware-bound)

- **No auth.** The server binds to localhost. Exposing it on a LAN or tailnet is
  possible (set `BLURT_HOST`) but should wait for an auth story before more than
  one trusted person can reach it.
- **Semantic speed is hardware-bound.** Bulk indexing runs at roughly 10-25
  notes/sec on a local model. Exact-text search is instant regardless; semantic
  catches up in the background.
- **One device.** No built-in sync. Your data is one SQLite file plus its
  `scratchpad.md` mirror; copy or back those up however you like.

## Ideas not yet built

- Packaged install (Homebrew / a real app shell) so it is not "clone and run."
- A simple in-product feedback path.
- Optional auth + multi-device sync.
- The `/` menu inside the in-stream editor (currently compose-box only).
