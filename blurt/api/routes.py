"""All HTTP endpoints.

Thin layer: validate, delegate to the core/db, shape the response. Long-running
work (embedding) is enqueued, never awaited on the request path, so saves return
in single-digit milliseconds.
"""

from __future__ import annotations

import re
from datetime import date

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..config import set_notes_dir, settings
from ..core import active_stream_markdown, render_stream_markdown
from ..core.checklist import set_checkbox
from ..core.dateref import anchor_dates
from .schemas import (
    CheckboxToggle,
    EntryCreate,
    EntryUpdate,
    NotesDirRequest,
    QueryRequest,
    SuggestRequest,
    SynthesizeRequest,
)

# Where update-check reads the latest published version. Raw main always carries the
# current __version__, so no releases/tags API or auth is needed.
_LATEST_VERSION_URL = "https://raw.githubusercontent.com/rbsriram/blurt/main/blurt/__init__.py"

router = APIRouter(prefix="/api")


# --- accessors ----------------------------------------------------------

def _db(request: Request):
    return request.app.state.db


def _indexer(request: Request):
    return request.app.state.indexer


def _retriever(request: Request):
    return request.app.state.retriever


def _touch_mirror(request: Request) -> None:
    """Mark the scratchpad.md mirror stale after a mutation (no-op if disabled)."""
    mirror = getattr(request.app.state, "mirror", None)
    if mirror is not None:
        mirror.schedule()


# --- status -------------------------------------------------------------

@router.get("/status")
async def status(request: Request):
    embedder = request.app.state.embedder
    connected, model_ok = await embedder.health()
    db = _db(request)
    return {
        "ollama_connected": connected,
        "embed_model_available": model_ok,
        "db_ok": db.health(),
        "entry_count": db.count_active_entries(),
        "indexing_pending": _indexer(request).pending(),
        "version": request.app.state.version,
        # Lets the UI surface the test-only erase control; off in any normal run.
        "testing": settings.enable_test_endpoints,
    }


# --- entries: capture & stream -----------------------------------------

@router.post("/entries", status_code=201)
async def create_entry(body: EntryCreate, request: Request):
    if len(body.content) > settings.max_content_chars:
        raise HTTPException(status_code=413, detail="content too large")
    db = _db(request)
    entry = db.add_entry(body.content)
    # Freeze any date references now, against today's local date, so "tomorrow"
    # means the day it was written, not the day it's later read. Pure + fast, so
    # it runs inline (no Ollama, unlike embedding) and is searchable immediately.
    db.set_entry_dates(entry["id"], anchor_dates(body.content, date.today()))
    _indexer(request).enqueue(entry["id"])
    _touch_mirror(request)
    return db.get_entry(entry["id"])


@router.get("/entries")
async def list_entries(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return {"entries": _db(request).list_entries(limit=limit, offset=offset)}


@router.get("/entries/{entry_id}")
async def get_entry(entry_id: int, request: Request):
    entry = _db(request).get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="entry not found")
    return entry


@router.patch("/entries/{entry_id}")
async def edit_entry(entry_id: int, body: EntryUpdate, request: Request):
    """Edit a note IN PLACE: same id, position, and timestamps.

    Editing updates the note where it sits rather than archiving the old version
    and appending a new one (which surfaced as a confusing "deleted" stub + the note
    jumping to the bottom). The text changed, so its embeddings are stale: clear them
    and re-enqueue for a background re-embed. Exact-text search works immediately.
    """
    if len(body.content) > settings.max_content_chars:
        raise HTTPException(status_code=413, detail="content too large")
    db = _db(request)
    updated = db.set_content_in_place(entry_id, body.content)
    if updated is None:
        existing = db.get_entry(entry_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="entry not found")
        raise HTTPException(status_code=409, detail="cannot edit a superseded entry")
    db.clear_chunks(entry_id)
    # Re-freeze dates against today: the edited text may add or drop references.
    db.set_entry_dates(entry_id, anchor_dates(body.content, date.today()))
    _indexer(request).enqueue(entry_id)
    _touch_mirror(request)
    return db.get_entry(entry_id)


@router.patch("/entries/{entry_id}/checkbox")
async def toggle_checkbox(entry_id: int, body: CheckboxToggle, request: Request):
    """Tick/untick one checkbox in place: same entry, no supersede, no re-embed.

    A checkbox flip is retrieval-neutral, so paying the supersede+re-embed cost of
    a full edit would be wasteful and would churn the note's id/position. We only
    rewrite the single marker character (server-side, by ordinal) and leave the
    vectors as they are. A real text edit still goes through PATCH (supersede).
    """
    db = _db(request)
    entry = db.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="entry not found")
    if entry["is_superseded"] == 1:
        raise HTTPException(status_code=409, detail="cannot edit a superseded entry")
    new_content = set_checkbox(entry["content"], body.index, body.checked)
    if new_content is None:
        raise HTTPException(status_code=409, detail="checkbox index out of range")
    updated = db.set_content_in_place(entry_id, new_content)
    if updated is None:  # raced with a supersede between our read and write
        raise HTTPException(status_code=409, detail="cannot edit a superseded entry")
    _touch_mirror(request)
    return updated


@router.delete("/entries/{entry_id}")
async def delete_entry(entry_id: int, request: Request):
    if not _db(request).supersede_entry(entry_id):
        raise HTTPException(status_code=404, detail="entry not found")
    _touch_mirror(request)
    return {"ok": True, "id": entry_id, "is_superseded": 1}


@router.patch("/entries/{entry_id}/restore")
async def restore_entry(entry_id: int, request: Request):
    entry = _db(request).restore_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="entry not found")
    _touch_mirror(request)
    return entry


@router.get("/entries/{entry_id}/chunks")
async def get_chunks(entry_id: int, request: Request):
    db = _db(request)
    if db.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail="entry not found")
    return {"chunks": db.get_chunks(entry_id)}


# --- ghost & search -----------------------------------------------------

@router.post("/suggest")
async def suggest(body: SuggestRequest, request: Request):
    return await _retriever(request).suggest(body.text)


@router.post("/query")
async def query(body: QueryRequest, request: Request):
    return await _retriever(request).query(body.query)


@router.post("/synthesize")
async def synthesize(body: SynthesizeRequest, request: Request):
    synth = request.app.state.synthesizer
    if synth is None:
        raise HTTPException(status_code=503, detail="LLM synthesis is disabled")
    db = _db(request)
    if body.entry_ids:
        entries = db.get_entries_by_ids(body.entry_ids)
    else:
        entries = (await _retriever(request).query(body.query))["entries"]
    answer = await synth.synthesize(body.query, entries)
    return {"answer": answer}


# --- export -------------------------------------------------------------

@router.get("/export/markdown")
async def export_markdown(request: Request, query: str | None = Query(None)):
    db = _db(request)
    if query:
        entries = [
            e
            for e in (await _retriever(request).query(query))["entries"]
            if e.get("score", 0.0) >= settings.export_query_min_score
        ]
        body = render_stream_markdown(entries)
    else:
        # Whole active stream, oldest first so the export reads chronologically.
        body = active_stream_markdown(db)
    return Response(content=body, media_type="text/markdown; charset=utf-8")


# --- settings -----------------------------------------------------------

@router.get("/settings")
async def get_settings(request: Request):
    return {
        "notes_dir": str(settings.notes_dir),
        "scratchpad_path": str(settings.export_md_path),
        "version": request.app.state.version,
    }


@router.post("/notes-dir")
async def change_notes_dir(body: NotesDirRequest, request: Request):
    """Point the readable scratchpad.md at a new folder, live. The index DB does not
    move, so a synced/cloud folder is safe. Writes the file at its new home immediately
    and removes the stale copy left in the old folder."""
    old_path = settings.export_md_path
    try:
        folder = set_notes_dir(settings.db_path, body.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    new_path = folder / "scratchpad.md"
    mirror = getattr(request.app.state, "mirror", None)
    if mirror is not None:
        mirror.set_path(new_path)
        await mirror.flush()
        if old_path != new_path and old_path.exists():
            try:
                old_path.unlink()
            except OSError:
                pass  # a leftover old mirror is harmless; do not fail the change over it
    return {"notes_dir": str(folder), "scratchpad_path": str(new_path)}


@router.get("/update-check")
async def update_check(request: Request):
    current = request.app.state.version
    latest = await _fetch_latest_version()
    if latest is None:
        return {"current": current, "latest": None, "update_available": False,
                "error": "Could not reach GitHub."}
    return {
        "current": current,
        "latest": latest,
        "update_available": _is_newer(latest, current),
        "command": "pipx upgrade blurt",
    }


async def _fetch_latest_version() -> str | None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(_LATEST_VERSION_URL)
            r.raise_for_status()
        m = re.search(r'__version__\s*=\s*"([^"]+)"', r.text)
        return m.group(1) if m else None
    except Exception:
        return None


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def _is_newer(latest: str, current: str) -> bool:
    """Compare dotted numeric versions; unparseable input is treated as 'not newer'."""
    try:
        return _version_tuple(latest) > _version_tuple(current)
    except Exception:
        return False


# --- test/dev only ------------------------------------------------------

@router.delete("/test/reset")
async def test_reset(request: Request):
    if not settings.enable_test_endpoints:
        raise HTTPException(status_code=404, detail="not found")
    _db(request).reset()
    return {"ok": True}
