"""Retrieval: the two read paths over the vector index.

- suggest()  powers the ghost. The text being typed is compared document-to-
  document against existing notes; if the single best active match clears the
  threshold, it surfaces. One match, never a list.
- query()    powers search. A natural-language question is embedded as a query,
  KNN'd against chunks, then collapsed to parent entries ranked by their best
  chunk, deduped, and capped.

The vec index only ever holds ACTIVE chunks, so both paths exclude superseded
entries for free.
"""

from __future__ import annotations

import asyncio
from datetime import date

from ..config import Settings
from ..db import Database
from .dateref import query_ranges
from .embedder import OllamaEmbedder


class Retriever:
    def __init__(self, db: Database, embedder: OllamaEmbedder, settings: Settings):
        self._db = db
        self._embedder = embedder
        self._s = settings

    async def suggest(self, text: str) -> dict:
        """Ghost peek: {'match', 'score', 'more', 'matches'}.

        'matches' is the full ranked list of ACTIVE notes that clear the
        threshold, each carrying its score, so the UI can show a keyboard-
        browsable peek (UX.md §2). 'match'/'score' stay the single best for the
        API contract; 'more' is how many OTHER notes also cleared (len-1).
        Superseded notes never enter the vec index, so the peek excludes them
        for free.
        """
        empty = {"match": None, "score": 0.0, "more": 0, "matches": []}
        if len(text.split()) < self._s.ghost_min_words_server:
            return empty

        # The peek is purely semantic, so if embeddings are unavailable (Ollama down or
        # slow) it simply shows nothing. Never let that raise: /suggest fires on every
        # keystroke, and a 500 storm helps no one.
        try:
            vec = await self._embedder.embed_document_one(text)
            hits = await asyncio.to_thread(self._db.knn, vec, 8)
        except Exception:
            return empty
        if not hits:
            return empty

        mapping = await asyncio.to_thread(self._db.chunk_entry_map, [c for c, _ in hits])
        best: dict[int, float] = {}
        for chunk_id, sim in hits:
            eid = mapping.get(chunk_id)
            if eid is not None and (eid not in best or sim > best[eid]):
                best[eid] = sim

        thr = self._s.ghost_similarity_threshold
        above = sorted(((eid, s) for eid, s in best.items() if s >= thr), key=lambda kv: -kv[1])
        if not above:
            return {**empty, "score": float(max(best.values(), default=0.0))}

        rows = await asyncio.to_thread(self._db.get_entries_by_ids, [eid for eid, _ in above])
        rowmap = {r["id"]: r for r in rows}
        matches = [
            {**rowmap[eid], "score": float(s)} for eid, s in above if eid in rowmap
        ]
        if not matches:
            return {**empty, "score": float(above[0][1])}
        top = matches[0]
        return {
            "match": top,
            "score": top["score"],
            "more": len(matches) - 1,
            "matches": matches,
        }

    async def query(self, q: str) -> dict:
        """Hybrid search: exact + date matches first (high-confidence), then semantic."""
        cap = self._s.query_max_entries

        # 1. Lexical: exact substring matches are high-confidence and immediate.
        lexical = await asyncio.to_thread(self._db.lexical_search, q, cap)

        # 1b. Date: if the query names a date ("tomorrow", "next week"), pull notes
        # whose frozen date lands in that range. Like lexical, this is exact and
        # embedding-independent, so it works the instant a note is saved and even
        # when Ollama is down. Resolved against the local "today".
        ranges = query_ranges(q, date.today())
        date_hits = (
            await asyncio.to_thread(self._db.entries_in_ranges, ranges, cap) if ranges else []
        )

        # 2. Semantic: vector KNN collapsed to parent entries by best chunk. Best-effort:
        # if embeddings are unavailable (Ollama down), exact matches must still return, so a
        # failure here degrades to lexical-only rather than sinking the whole search.
        best: dict[int, float] = {}
        try:
            vec = await self._embedder.embed_query(q)
            hits = await asyncio.to_thread(self._db.knn, vec, self._s.query_top_chunks)
            if hits:
                mapping = await asyncio.to_thread(self._db.chunk_entry_map, [c for c, _ in hits])
                for chunk_id, sim in hits:
                    eid = mapping.get(chunk_id)
                    if eid is not None and (eid not in best or sim > best[eid]):
                        best[eid] = sim
        except Exception:
            pass  # Ollama unreachable/slow: return the lexical hits we already have
        ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
        rows = await asyncio.to_thread(self._db.get_entries_by_ids, [eid for eid, _ in ranked])
        rowmap = {r["id"]: r for r in rows}

        # 3. Merge: exact (lexical + date) hits lead at full confidence, semantic
        # fills the rest, deduped, capped. Lexical leads date so a literal text
        # match still wins its slot; date hits carry a flag the UI can lean on.
        seen: set[int] = set()
        entries: list[dict] = []
        for e in lexical:
            if e["id"] not in seen:
                entries.append({**e, "score": 1.0})
                seen.add(e["id"])
        for e in date_hits:
            if e["id"] not in seen:
                entries.append({**e, "score": 1.0, "date_match": True})
                seen.add(e["id"])
        for eid, sim in ranked:
            if eid in seen:
                continue
            row = rowmap.get(eid)
            if row is not None:
                entries.append({**row, "score": float(sim)})
                seen.add(eid)
        return {"entries": entries[:cap], "chunks": []}
