# STATE — project status

_A living snapshot of where Blurt is. Update it as things change._

## Where it is

Blurt is **shipped: public, open source (MIT) at
[github.com/rbsriram/blurt](https://github.com/rbsriram/blurt), v1.4.0.** Install is
`pipx install git+https://github.com/rbsriram/blurt` then `blurt` (see the README);
a `blurt/cli.py` launcher checks Ollama, starts the server, and opens Blurt in its
own native desktop window (`blurt/desktop.py`, via pywebview; `BLURT_BROWSER=1`
falls back to a browser tab). On macOS the install itself adds a double-clickable
`blurt.app` (dock icon, lowercase "blurt" brand, a conventional App/Edit/View/Window/Help
menu) to `~/Applications`: `blurt/installer.py` writes a thin bundle that execs
`sys.executable -m blurt.cli`, so it works for any install method (pipx/pip/bootstrap),
created silently on first run (once; a `.app-added` marker stops it resurrecting a bundle
the user trashed) or via `blurt install-app`. The window sizes to the screen and remembers
its geometry; the menu bar (blurt/File/Edit/View/Window/Help) is built in `blurt/desktop.py`.
A Settings pane (`⌘,`) holds the notes-folder choice and an update check; `scratchpad.md`
can live in any folder (e.g. an Obsidian/Dropbox folder) while the index DB stays internal.
`blurt uninstall` removes the app and leaves notes alone. The icon ships in the wheel
(`blurt/assets/Blurt.icns`, plus `static/blurt-icon.png` for the splash). CI (lint + import
smoke + offline unit tests) is green; Issues + Discussions are on.

Feature-complete for v1. Backend and front end are both built to `docs/UX.md`,
tested, and linted.

- **Backend:** full API, hybrid search (semantic + exact), active-only vector
  index, incremental background indexing, model kept warm, localhost bind, DB file
  locked to `chmod 600`, and an always-current `scratchpad.md` mirror.
- **Front end:** bottom-pinned capture, the keyboard-browsable peek, `↑`-from-an-empty-box
  stream navigation (walk recent notes, enter to edit; see DECISIONS #56), in-place note
  editing, arrow-navigable search, checklists, list auto-continuation, a `/`
  formatting menu, auto-linked URLs, Markdown export, and a `?` cheatsheet.
- **Date-aware notes:** date phrases ("tomorrow", "next friday", "Jun 1", "14/2/2024")
  are frozen to absolute days at capture and shown as a subtle, clickable label; you
  can search by date ("tomorrow", "next week", "2nd feb") and a Settings toggle picks
  day-first vs month-first for ambiguous numeric dates. It is a search enhancer only,
  never a task/calendar app. See DECISIONS #54.
- **Encrypted secrets:** jot a credential with `⌘K` / `/secret`; the value is encrypted
  at rest (Fernet, key in the OS keychain), masked, copyable, and kept out of the
  mirror and the search index. Click to edit, empty + Enter to delete. A safer place
  to jot a credential, not a password manager. See DECISIONS #55 and `docs/SECURITY.md`.
- **Editing/deleting:** any note deletes the text-pad way (open, clear, Enter); the
  per-note `×` is gone. `Esc` returns focus to the input box from anywhere.
- **Smart-search engine state:** the pad is gated on first launch until Ollama + the
  embedding model are ready (so notes are never saved unindexable); after that an Ollama
  drop is non-blocking (capture + exact search continue, the peek resumes on recovery).
  The indexer self-heals: it pulls the model if Ollama appears without it, and re-indexes
  any backlog. Settings shows the live engine status. See DECISIONS #53.
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

- **No auth.** The server binds to localhost, and Host-header validation
  (anti-DNS-rebinding, see DECISIONS #39) blocks browser-based attempts at the local
  API. Exposing it on a LAN or tailnet is possible (set `BLURT_HOST`) but should wait
  for an auth story before more than one trusted person can reach it.
- **Semantic speed is hardware-bound.** Bulk indexing runs at roughly 10-25
  notes/sec on a local model. Exact-text search is instant regardless; semantic
  catches up in the background.
- **One device.** No built-in sync. Your data is one SQLite file plus its
  `scratchpad.md` mirror; copy or back those up however you like.

## Ideas not yet built

A "what's on the pad" style `ROADMAP.md` was drafted and **parked** (owner wants to
revisit it). The honest shortlist:

- A small native app shell so Blurt gets a dock icon (pip/pipx install already done).
- A simple, explicit in-product feedback path (no telemetry, ever).
- Optional auth + peer-to-peer multi-device sync (no cloud middleman).
- The `/` menu inside the in-stream editor (currently compose-box only).
- Self-renumbering ordered lists; Cmd-clickable links in search results.

The launch follow-ups (a Show HN / Reddit post in the owner's voice) are also pending;
all public copy goes through the owner first.
