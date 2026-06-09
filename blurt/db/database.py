"""SQLite + sqlite-vec storage layer.

Design notes:
- One process-wide connection, guarded by a re-entrant lock. Every method here
  is synchronous and serialized; async callers wrap them in asyncio.to_thread so
  blocking SQLite work never stalls the event loop. For a single-user local app
  this is simpler and more correct than a connection pool.
- chunks.embedding is the durable source of truth. vec_chunks (a vec0 virtual
  table) is a derived ANN index that holds vectors for ACTIVE chunks only.
  Superseding an entry deletes its vectors from the index; restoring re-inserts
  them from the stored BLOB. So search naturally excludes retired entries
  without ever re-embedding.
- chunks.id == vec_chunks.rowid. That id is the join key between a KNN hit and
  its parent entry.
"""

from __future__ import annotations

import os
import sqlite3
import struct
import threading
from pathlib import Path

import sqlite_vec

ENTRY_COLUMNS = "id, content, created_at, is_superseded, superseded_by, superseded_at, indexed_at"


def serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def deserialize_f32(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


class Database:
    def __init__(self, path: str, embed_dim: int):
        self.path = path
        self.embed_dim = embed_dim
        self._lock = threading.RLock()
        self._conn = self._connect()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        # Your notes are private. Lock the file to owner read/write only.
        if self.path != ":memory:" and os.path.exists(self.path):
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _init_schema(self) -> None:
        schema = (Path(__file__).parent / "schema.sql").read_text()
        with self._lock:
            self._conn.executescript(schema)
            # The vector index dimension is config-driven, so its DDL lives here.
            self._conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks "
                f"USING vec0(embedding float[{self.embed_dim}] distance_metric=cosine)"
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- helpers -------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: sqlite3.Row | None) -> dict | None:
        return dict(row) if row is not None else None

    def _with_dates(self, entries: list[dict]) -> list[dict]:
        """Attach each entry's frozen date references as a sorted ISO list.

        One batched query for the whole set, so listing the stream or a page of
        search results stays a single round trip rather than N+1.
        """
        if not entries:
            return entries
        ids = [e["id"] for e in entries]
        placeholders = ",".join("?" * len(ids))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT entry_id, date FROM entry_dates WHERE entry_id IN ({placeholders}) "
                f"ORDER BY date",
                ids,
            ).fetchall()
        by_entry: dict[int, list[str]] = {}
        for r in rows:
            by_entry.setdefault(r["entry_id"], []).append(r["date"])
        for e in entries:
            e["dates"] = by_entry.get(e["id"], [])
        return entries

    # ---- entries -------------------------------------------------------

    def add_entry(self, content: str) -> dict:
        with self._lock:
            cur = self._conn.execute("INSERT INTO entries(content) VALUES (?)", (content,))
            self._conn.commit()
            return self.get_entry(cur.lastrowid)  # type: ignore[arg-type]

    def get_entry(self, entry_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {ENTRY_COLUMNS} FROM entries WHERE id = ?", (entry_id,)
            ).fetchone()
        entry = self._row_to_entry(row)
        return self._with_dates([entry])[0] if entry is not None else None

    def list_entries(self, limit: int = 50, offset: int = 0) -> list[dict]:
        # Newest first. id is monotonic with insert order, so ordering by it is
        # both correct and immune to same-millisecond timestamp ties.
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {ENTRY_COLUMNS} FROM entries ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return self._with_dates([dict(r) for r in rows])

    def count_active_entries(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM entries WHERE is_superseded = 0"
            ).fetchone()[0]

    def supersede_entry(self, entry_id: int, superseded_by: int | None = None) -> bool:
        """Mark an active entry retired and pull its vectors from the index."""
        with self._lock:
            entry = self.get_entry(entry_id)
            if entry is None:
                return False
            self._conn.execute(
                "UPDATE entries SET is_superseded = 1, "
                "superseded_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), superseded_by = ? "
                "WHERE id = ?",
                (superseded_by, entry_id),
            )
            self._deindex_entry(entry_id)
            self._conn.commit()
            return True

    def restore_entry(self, entry_id: int) -> dict | None:
        with self._lock:
            entry = self.get_entry(entry_id)
            if entry is None:
                return None
            self._conn.execute(
                "UPDATE entries SET is_superseded = 0, superseded_at = NULL, superseded_by = NULL "
                "WHERE id = ?",
                (entry_id,),
            )
            self._reindex_entry(entry_id)
            self._conn.commit()
            return self.get_entry(entry_id)

    def set_content_in_place(self, entry_id: int, new_content: str) -> dict | None:
        """Rewrite an active entry's content in place: same id, position, timestamps.

        Editing a note updates it where it sits (it does NOT archive the old version
        and append a new one). Returns None if the entry is missing or superseded.
        Callers that change the *text* must also re-index (clear_chunks + enqueue);
        a checkbox tick leaves the vectors alone since the embedding is unchanged.
        """
        with self._lock:
            entry = self.get_entry(entry_id)
            if entry is None or entry["is_superseded"] == 1:
                return None
            self._conn.execute(
                "UPDATE entries SET content = ? WHERE id = ?", (new_content, entry_id)
            )
            self._conn.commit()
            return self.get_entry(entry_id)

    # ---- chunks / index ------------------------------------------------

    def add_chunks(self, entry_id: int, chunks: list[tuple[int, str, list[float]]]) -> None:
        """Store chunk rows + embeddings, then index active entries' vectors."""
        with self._lock:
            entry = self.get_entry(entry_id)
            if entry is None:  # entry deleted out from under us
                return
            is_active = entry["is_superseded"] == 0
            for idx, text, vec in chunks:
                blob = serialize_f32(vec)
                cur = self._conn.execute(
                    "INSERT INTO chunks(entry_id, chunk_index, chunk_text, embedding) "
                    "VALUES (?, ?, ?, ?)",
                    (entry_id, idx, text, blob),
                )
                if is_active:
                    self._conn.execute(
                        "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                        (cur.lastrowid, blob),
                    )
            self._conn.execute(
                "UPDATE entries SET indexed_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
                (entry_id,),
            )
            self._conn.commit()

    def get_chunks(self, entry_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, entry_id, chunk_index, chunk_text, embedding, created_at "
                "FROM chunks WHERE entry_id = ? ORDER BY chunk_index",
                (entry_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["embedding"] = deserialize_f32(d["embedding"]) if d["embedding"] is not None else None
            out.append(d)
        return out

    def clear_chunks(self, entry_id: int) -> None:
        """Drop an entry's chunks + vectors so it can be re-indexed from scratch.

        Used when a note's text is edited in place: the old embeddings are stale,
        so we wipe them and re-enqueue the entry; until the background re-embed
        lands, exact-text search still finds the new content, semantic catches up.
        """
        with self._lock:
            self._deindex_entry(entry_id)
            self._conn.execute("DELETE FROM chunks WHERE entry_id = ?", (entry_id,))
            self._conn.commit()

    def _deindex_entry(self, entry_id: int) -> None:
        # caller holds the lock
        self._conn.execute(
            "DELETE FROM vec_chunks WHERE rowid IN (SELECT id FROM chunks WHERE entry_id = ?)",
            (entry_id,),
        )

    def _reindex_entry(self, entry_id: int) -> None:
        # caller holds the lock
        rows = self._conn.execute(
            "SELECT id, embedding FROM chunks WHERE entry_id = ? AND embedding IS NOT NULL",
            (entry_id,),
        ).fetchall()
        for r in rows:
            self._conn.execute(
                "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)", (r["id"], r["embedding"])
            )

    def unindexed_active_ids(self, limit: int) -> list[int]:
        """Active entries that have no chunks yet, oldest first.

        A note with content always yields at least one chunk once embedded, so "no
        chunks" means "not indexed". The self-heal pass uses this to re-embed notes
        that were saved while Ollama was unavailable, the moment it returns.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM entries WHERE is_superseded = 0 "
                "AND id NOT IN (SELECT entry_id FROM chunks) ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
        return [r["id"] for r in rows]

    def lexical_search(self, query: str, limit: int) -> list[dict]:
        """Exact (case-insensitive) substring match over active entries.

        Complements vector search: exact tokens (codes, IDs, phone numbers) are
        findable the instant they are saved, before embeddings land.
        """
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {ENTRY_COLUMNS} FROM entries "
                f"WHERE is_superseded = 0 AND content LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
                (pattern, limit),
            ).fetchall()
        return self._with_dates([dict(r) for r in rows])

    def entries_in_ranges(self, ranges: list[tuple[str, str]], limit: int) -> list[dict]:
        """Active entries with a frozen date inside any (start, end) ISO range.

        Powers date search: a query like "next week" resolves to a range, and any
        note whose anchored date lands in it surfaces, soonest date first. Bounds
        are inclusive. Superseded notes are excluded, matching every other read.
        """
        if not ranges:
            return []
        clauses = " OR ".join("ed.date BETWEEN ? AND ?" for _ in ranges)
        params: list = []
        for start, end in ranges:
            params.extend([start, end])
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {', '.join('e.' + c for c in ENTRY_COLUMNS.split(', '))}, "
                f"MIN(ed.date) AS match_date "
                f"FROM entries e JOIN entry_dates ed ON ed.entry_id = e.id "
                f"WHERE e.is_superseded = 0 AND ({clauses}) "
                f"GROUP BY e.id ORDER BY match_date ASC, e.id DESC LIMIT ?",
                params,
            ).fetchall()
        out = [dict(r) for r in rows]
        for e in out:
            e.pop("match_date", None)  # ordering helper, not part of the entry shape
        return self._with_dates(out)

    def set_entry_dates(self, entry_id: int, dates: list[str]) -> None:
        """Replace the frozen date references for an entry (idempotent on re-save)."""
        with self._lock:
            self._conn.execute("DELETE FROM entry_dates WHERE entry_id = ?", (entry_id,))
            self._conn.executemany(
                "INSERT OR IGNORE INTO entry_dates(entry_id, date) VALUES (?, ?)",
                [(entry_id, d) for d in dates],
            )
            self._conn.commit()

    # Bump when the date parser changes so existing notes get re-frozen with the
    # newer logic on next launch (a no-op once they're already at this version).
    _DATES_PARSER_VERSION = "2"

    def backfill_dates(self, resolve) -> int:
        """Freeze date references for all notes, re-running when the parser changes.

        Each note is anchored to its OWN creation date, so a relative phrase resolves
        to what it meant when written, not to today. ``resolve(content, day)`` is the
        date resolver, injected so the storage layer stays unaware of how parsing
        works. Skipped entirely once notes are already at the current parser version;
        returns the number of notes processed.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = 'dates_parser_version'"
            ).fetchone()
            if row is not None and row["value"] == self._DATES_PARSER_VERSION:
                return 0
            rows = self._conn.execute(
                "SELECT id, content, created_at FROM entries WHERE is_superseded = 0"
            ).fetchall()
        from datetime import date
        for r in rows:
            day = date.fromisoformat(r["created_at"][:10])
            self.set_entry_dates(r["id"], resolve(r["content"], day))
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('dates_parser_version', ?)",
                (self._DATES_PARSER_VERSION,),
            )
            self._conn.commit()
        return len(rows)

    def knn(self, query_vec: list[float], k: int) -> list[tuple[int, float]]:
        """Return (chunk_id, similarity) for the k nearest active chunks."""
        q = serialize_f32(query_vec)
        with self._lock:
            rows = self._conn.execute(
                "SELECT rowid, distance FROM vec_chunks "
                "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (q, k),
            ).fetchall()
        # cosine distance -> similarity
        return [(r["rowid"], 1.0 - r["distance"]) for r in rows]

    def entry_for_chunk(self, chunk_id: int) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT entry_id FROM chunks WHERE id = ?", (chunk_id,)
            ).fetchone()
        return row["entry_id"] if row else None

    def chunk_entry_map(self, chunk_ids: list[int]) -> dict[int, int]:
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" * len(chunk_ids))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, entry_id FROM chunks WHERE id IN ({placeholders})", chunk_ids
            ).fetchall()
        return {r["id"]: r["entry_id"] for r in rows}

    def get_entries_by_ids(self, entry_ids: list[int]) -> list[dict]:
        if not entry_ids:
            return []
        placeholders = ",".join("?" * len(entry_ids))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {ENTRY_COLUMNS} FROM entries WHERE id IN ({placeholders})", entry_ids
            ).fetchall()
        return self._with_dates([dict(r) for r in rows])

    # ---- maintenance ---------------------------------------------------

    def health(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def reset(self) -> None:
        """Wipe everything. Test/dev only."""
        with self._lock:
            self._conn.execute("DELETE FROM vec_chunks")
            self._conn.execute("DELETE FROM entry_dates")
            self._conn.execute("DELETE FROM chunks")
            self._conn.execute("DELETE FROM entries")
            self._conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('entries','chunks')")
            self._conn.commit()
