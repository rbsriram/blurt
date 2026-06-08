"""Background indexing worker.

Saving an entry must feel instant, so embedding happens off the request path. A
single asyncio worker drains a queue, and crucially it BATCHES: when entries
pile up (bulk paste, imports, stress) it embeds many chunks per Ollama call
instead of one-at-a-time. That is the difference between a snappy app and one
that crawls under load.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from ..config import Settings
from ..db import Database
from .chunker import chunk_text
from .embedder import OllamaEmbedder

log = logging.getLogger("blurt.indexer")


class Indexer:
    def __init__(self, db: Database, embedder: OllamaEmbedder, settings: Settings):
        self._db = db
        self._embedder = embedder
        self._s = settings
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._inflight: set[int] = set()  # queued-or-processing ids, so reconcile never double-enqueues
        self._task: asyncio.Task | None = None
        self._heal_task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="blurt-indexer")
        if self._heal_task is None:
            self._heal_task = asyncio.create_task(self._heal_loop(), name="blurt-indexer-heal")

    async def stop(self) -> None:
        for attr in ("_task", "_heal_task"):
            task = getattr(self, attr)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                setattr(self, attr, None)

    def enqueue(self, entry_id: int) -> None:
        if entry_id in self._inflight:  # already queued or being indexed; don't pile on a dupe
            return
        self._inflight.add(entry_id)
        self._queue.put_nowait(entry_id)

    def pending(self) -> int:
        return self._queue.qsize()

    async def wait_idle(self) -> None:
        """Block until the queue is fully drained (used by tests)."""
        await self._queue.join()

    async def _run(self) -> None:
        while True:
            entry_id = await self._queue.get()
            batch = [entry_id]
            # Opportunistically drain whatever else is waiting so we can embed
            # many entries' chunks in a few calls instead of one call each.
            while len(batch) < self._s.index_drain_cap and not self._queue.empty():
                batch.append(self._queue.get_nowait())
            try:
                await self._index_entries(batch)
            except Exception:  # noqa: BLE001 - worker must never die
                # Most likely Ollama is down. The entries keep their "no chunks" state, so the
                # self-heal pass re-enqueues them once it is back; here we just drop them from
                # the in-flight set so that can happen.
                log.exception("indexing batch failed: %s", batch)
            finally:
                for eid in batch:
                    self._inflight.discard(eid)
                    self._queue.task_done()

    async def _heal_loop(self) -> None:
        """Keep the index whole without a restart. Each pass: pull the embedding model if
        Ollama just became reachable but lacks it, then re-enqueue any active notes that have
        no chunks yet (saved while Ollama was down). When Ollama is unreachable, ensure_model
        returns False and we skip, capture and exact search carry on regardless."""
        while True:
            await asyncio.sleep(self._s.reconcile_interval_s)
            try:
                if not await self._embedder.ensure_model():
                    continue
                ids = await asyncio.to_thread(
                    self._db.unindexed_active_ids, self._s.index_drain_cap
                )
                for eid in ids:
                    self.enqueue(eid)
            except Exception:  # noqa: BLE001 - the heal loop must never die either
                log.exception("self-heal pass failed")

    async def _index_entries(self, entry_ids: list[int]) -> None:
        # Process in groups so entries become searchable incrementally during a
        # bulk insert, rather than all-or-nothing at the end. Each group is one
        # embed call, bounding both latency and time-to-first-result.
        bs = self._s.embed_batch_size
        for start in range(0, len(entry_ids), bs):
            await self._index_group(entry_ids[start : start + bs])

    async def _index_group(self, entry_ids: list[int]) -> None:
        specs: list[tuple[int, int, str]] = []  # (entry_id, chunk_index, text)
        for eid in entry_ids:
            entry = await asyncio.to_thread(self._db.get_entry, eid)
            if entry is None:
                continue
            chunks = chunk_text(
                entry["content"],
                single_max_words=self._s.chunk_single_max_words,
                size_words=self._s.chunk_size_words,
                overlap_words=self._s.chunk_overlap_words,
            )
            for i, text in enumerate(chunks):
                specs.append((eid, i, text))

        if not specs:
            return

        # A single group is usually one embed call; a few long notes may spill
        # into extra calls, which is fine.
        texts = [s[2] for s in specs]
        vectors: list[list[float]] = []
        bs = self._s.embed_batch_size
        for i in range(0, len(texts), bs):
            vectors.extend(await self._embedder.embed_documents(texts[i : i + bs]))

        by_entry: dict[int, list[tuple[int, str, list[float]]]] = defaultdict(list)
        for (eid, idx, text), vec in zip(specs, vectors, strict=True):
            by_entry[eid].append((idx, text, vec))

        for eid, payload in by_entry.items():
            await asyncio.to_thread(self._db.add_chunks, eid, payload)
