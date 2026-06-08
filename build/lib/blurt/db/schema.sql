-- Blurt storage schema.
-- One append-only stream of entries; each entry fans out into chunks for
-- semantic search. The vector index (vec_chunks) is a vec0 virtual table
-- created separately in database.py because its dimension is config-driven.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- The stream. Append-only in spirit: edits create a new row and mark the old
-- one superseded rather than mutating history.
CREATE TABLE IF NOT EXISTS entries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    content        TEXT    NOT NULL,
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    is_superseded  INTEGER NOT NULL DEFAULT 0,       -- 0 = active, 1 = retired
    superseded_by  INTEGER REFERENCES entries(id),   -- the row that replaced this one
    superseded_at  TEXT,
    indexed_at     TEXT                              -- set when embeddings land
);

-- One entry = one or more chunks. chunk.id is the rowid used in the vector
-- index, so it is the join key between KNN hits and their parent entry.
CREATE TABLE IF NOT EXISTS chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id     INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    chunk_index  INTEGER NOT NULL,
    chunk_text   TEXT    NOT NULL,
    embedding    BLOB,                               -- float32 vector, source of truth
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_entries_created    ON entries(created_at);
CREATE INDEX IF NOT EXISTS idx_entries_active     ON entries(is_superseded);
CREATE INDEX IF NOT EXISTS idx_chunks_entry       ON chunks(entry_id);

-- Lightweight key/value for schema versioning and bookkeeping.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
