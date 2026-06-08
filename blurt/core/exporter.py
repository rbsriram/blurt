"""Markdown export: the on-demand download and the always-current file mirror.

Notes are stored verbatim, so a Markdown rendering of the stream is lossless and
app-independent. Two consumers share one renderer (`render_stream_markdown`):

  * `GET /api/export/markdown` streams it on demand (whole stream or a query view).
  * `MarkdownMirror` keeps a `scratchpad.md` next to `blurt.db` continuously in
    sync, so a human-readable, Blurt-independent copy of everything always exists
    on disk. The DB stays the fast source of truth; the file is a free mirror.

The mirror writes off the request path and DEBOUNCES: a burst of saves collapses
into one rewrite, and the write is atomic (temp file + os.replace) so a reader
never sees a half-written file.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from ..db import Database

log = logging.getLogger("blurt.exporter")


def render_stream_markdown(entries: list[dict]) -> str:
    """Render entries (oldest-first) to Markdown. Empty input -> empty string."""
    if not entries:
        return ""
    lines = ["# Scratchpad\n"]
    for e in entries:
        lines.append(f"## {e['created_at']}\n")
        lines.append(e["content"].rstrip() + "\n")
    return "\n".join(lines)


def active_stream_markdown(db: Database) -> str:
    """The whole active stream, oldest-first, as Markdown (reads chronologically)."""
    entries = [e for e in db.list_entries(limit=10**9, offset=0) if e["is_superseded"] == 0]
    entries.reverse()
    return render_stream_markdown(entries)


class MarkdownMirror:
    """Keeps a single rolling Markdown file in sync with the active stream.

    `schedule()` is fire-and-forget and cheap: it just (re)arms a debounce timer.
    The actual render+write runs on a worker task after the stream settles, so
    saving never waits on disk. `flush()` forces an immediate write (used at
    shutdown so the file reflects the final state).
    """

    def __init__(self, db: Database, path: Path, debounce_s: float):
        self._db = db
        self._path = path
        self._debounce_s = debounce_s
        self._dirty = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="blurt-mirror")
            # Write once at boot so the file exists and matches current state even
            # if nothing is saved this session.
            self.schedule()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.flush()

    def schedule(self) -> None:
        """Mark the mirror stale; the worker coalesces and writes after the debounce."""
        self._dirty.set()

    @property
    def path(self) -> Path:
        return self._path

    def set_path(self, path: Path) -> None:
        """Point the mirror at a new file (e.g. the user picked a different folder).
        The caller should flush() afterward to write the file at its new home now."""
        self._path = path

    async def _run(self) -> None:
        while True:
            await self._dirty.wait()
            # Coalesce a burst: wait out the debounce window, absorbing more saves.
            await asyncio.sleep(self._debounce_s)
            self._dirty.clear()
            try:
                await self.flush()
            except Exception:  # noqa: BLE001 - a mirror failure must never break the app
                log.exception("scratchpad.md mirror write failed")

    async def flush(self) -> None:
        """Render the active stream and atomically replace the file on disk."""
        body = await asyncio.to_thread(active_stream_markdown, self._db)
        await asyncio.to_thread(self._write_atomic, body)

    def _write_atomic(self, body: str) -> None:
        # Temp file in the same dir guarantees os.replace is an atomic rename
        # (same filesystem); a reader sees either the old or the new file, never
        # a partial one.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, self._path)
