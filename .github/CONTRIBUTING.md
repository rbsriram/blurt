# Contributing to Blurt

Thanks for considering a contribution. Blurt has a deliberately narrow scope, so
the most useful first step is a quick issue describing what you want to change.

## Philosophy (read this before proposing features)

Blurt is one append-only stream with intelligence in retrieval, not in input.
Some things are permanent non-goals, closed to protect the product:

- folders, tags, categories
- rich-text / WYSIWYG editing
- collaboration / shared scratchpads
- cloud storage by default
- a native mobile app (mobile web is enough)

If a feature adds organization burden to capture, it is probably out of scope.

## Setup

```bash
./setup.sh
./.venv/bin/python main.py
```

## Before opening a PR

```bash
./.venv/bin/ruff check blurt main.py tests   # lint
./.venv/bin/pytest tests/test_unit.py -q       # offline tests

# Full integration suite (needs Ollama running):
./.venv/bin/python main.py &                   # start the server
BLURT_TESTING=1 ./.venv/bin/python main.py     # OR run with test endpoints
./.venv/bin/pytest docs/test_suite.py -q
```

## Code taste

- Keep modules small and single-purpose. The seams (embedder, retriever,
  database) are swappable on purpose; do not couple them.
- No new runtime dependencies without a strong reason. The whole point is that
  this runs locally with almost nothing installed.
- Match the surrounding style. Comments explain *why*, not *what*.
