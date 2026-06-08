"""FastAPI application factory and lifecycle wiring.

Everything the app needs is constructed once in the lifespan and hung off
app.state: the DB connection, the embedder, the background indexer, the
retriever, and (only if enabled) the synthesizer. Teardown is the mirror image.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from . import __version__
from .api import router
from .config import settings
from .core import Indexer, MarkdownMirror, OllamaEmbedder, Retriever
from .core.synthesizer import Synthesizer
from .db import Database

log = logging.getLogger("blurt")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database(settings.db_path, settings.embed_dim)
    embedder = OllamaEmbedder(
        url=settings.ollama_url,
        model=settings.embed_model,
        dim=settings.embed_dim,
        use_prefixes=settings.embed_use_prefixes,
        timeout_s=settings.embed_timeout_s,
        keep_alive=settings.embed_keep_alive,
    )
    indexer = Indexer(db, embedder, settings)
    indexer.start()
    retriever = Retriever(db, embedder, settings)
    mirror = (
        MarkdownMirror(db, settings.export_md_path, settings.auto_export_debounce_s)
        if settings.auto_export_md
        else None
    )
    if mirror is not None:
        mirror.start()
    synthesizer = (
        Synthesizer(url=settings.ollama_url, model=settings.chat_model)
        if settings.chat_enabled
        else None
    )

    app.state.db = db
    app.state.embedder = embedder
    app.state.indexer = indexer
    app.state.retriever = retriever
    app.state.synthesizer = synthesizer
    app.state.mirror = mirror
    app.state.version = __version__

    log.info("Blurt %s ready on http://%s:%s", __version__, settings.host, settings.port)
    try:
        yield
    finally:
        if mirror is not None:
            await mirror.stop()
        await indexer.stop()
        await embedder.aclose()
        if synthesizer is not None:
            await synthesizer.aclose()
        db.close()


class _NoCacheStatic(StaticFiles):
    """Serve the UI with `Cache-Control: no-cache` so the browser always revalidates.

    With only an ETag/Last-Modified (Starlette's default) browsers heuristically cache
    app.js/style.css and skip revalidation, so edits to the UI silently don't show up on
    reload. `no-cache` still allows 304s (cheap) but guarantees fresh code every load —
    the right call for a local-first app the owner reloads constantly.
    """

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app() -> FastAPI:
    app = FastAPI(title="Blurt", version=__version__, lifespan=lifespan)
    app.include_router(router)
    # Static UI is the catch-all, registered last so it never shadows /api.
    app.mount("/", _NoCacheStatic(directory=str(settings.static_dir), html=True), name="static")
    return app
