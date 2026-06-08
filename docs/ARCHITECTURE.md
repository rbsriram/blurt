# ARCHITECTURE

## Layers

```
main.py                 entry point; uvicorn on 127.0.0.1
blurt/
  config.py             Settings dataclass; all values env-overridable (BLURT_*)
  app.py                FastAPI factory + lifespan (constructs everything, hangs it on app.state)
  db/
    schema.sql          entries + chunks + meta tables, indexes
    database.py         Database: lock-guarded sync SQLite + sqlite-vec; all storage logic
  core/
    embedder.py         OllamaEmbedder: async batch embeddings w/ nomic prefixes
    chunker.py          pure chunk_text()
    indexer.py          async background worker: queue -> embed (batched, incremental) -> store
    retriever.py        suggest() (ghost) + query() (hybrid lexical+vector)
    synthesizer.py      optional LLM answer synthesis (off by default)
  api/
    schemas.py          pydantic request models + validation
    routes.py           all /api/* endpoints
  static/
    index.html / style.css / app.js   vanilla UI, no build step
tests/test_unit.py      offline unit tests (CI)
docs/test_suite.py      integration suite (needs live server + Ollama)
scripts/                startup.sh, winddown.sh (session lifecycle)
```

## Request lifecycles

**Save** (`POST /api/entries`)
1. Validate (reject empty/whitespace/null-byte; never mutate content).
2. Insert into `entries` (instant). Return 201 immediately.
3. Enqueue entry id on the indexer. Embedding happens later, off the request path.

**Index** (background worker)
1. Drain queued entry ids (up to `index_drain_cap`).
2. In groups of `embed_batch_size`: chunk each entry, embed chunks via Ollama
   (`search_document:` prefix), store chunk rows + BLOBs, and add active chunks'
   vectors to `vec_chunks`. Storing per group makes entries searchable
   progressively during bulk inserts.

**Ghost** (`POST /api/suggest`)
1. Reject if below the server word floor.
2. Embed the typed text as a document; KNN top-1 over active chunks.
3. If similarity ≥ threshold, return the parent entry + score; else null.

**Search** (`POST /api/query`) — hybrid
1. Lexical: exact substring match over active entries (instant, embedding-
   independent).
2. Semantic: embed query (`search_query:` prefix), KNN, collapse chunks to
   parent entries by best score.
3. Merge: lexical hits lead, semantic fills, dedupe, cap at `query_max_entries`.

**Edit / supersede / restore**
- Edit = `replace_entry`: insert new entry, mark old superseded (`superseded_by`
  = new id), pull old vectors from the index. New entry is re-enqueued for
  indexing.
- Supersede (`DELETE`) = mark superseded + pull vectors.
- Restore = unmark + re-insert vectors from stored BLOBs (no re-embedding).

## Key data invariant

`chunks` is the durable source of truth (text + embedding BLOB). `vec_chunks` is
a derived ANN index containing **only active** chunks. `chunks.id ==
vec_chunks.rowid`. Keeping the index active-only is what makes superseded notes
vanish from search/ghost without any per-query filtering.

## Concurrency model

One SQLite connection, guarded by a re-entrant lock; every `Database` method is
synchronous and serialized. Async callers (routes, indexer) invoke them via
`asyncio.to_thread`, so blocking SQLite never stalls the event loop. The
embedder uses one shared async httpx client. Simple, correct, single-user-scale.

## Why these choices

See `docs/DECISIONS.md`.
