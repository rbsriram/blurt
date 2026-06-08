"""Offline unit tests: no Ollama, no server, fast. These run in CI.

The full behavioral contract lives in docs/test_suite.py (integration, needs a
live server + Ollama). These cover the pure logic and the storage layer so
regressions are caught without external services.
"""

import asyncio
import os

import pytest

from blurt.api.schemas import CheckboxToggle, EntryCreate, QueryRequest
from blurt.config import Settings
from blurt.core.checklist import set_checkbox
from blurt.core.chunker import chunk_text
from blurt.core.exporter import render_stream_markdown
from blurt.core.indexer import Indexer
from blurt.core.retriever import Retriever
from blurt.db import Database

DIM = 8


def vec(seed: float) -> list[float]:
    return [seed] + [0.0] * (DIM - 1)


# ---- chunker ----------------------------------------------------------

def test_chunker_empty():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_chunker_short_is_single_verbatim():
    text = "Gate code is 44321.\nSide entrance."
    chunks = chunk_text(text, single_max_words=100)
    assert chunks == [text]  # formatting preserved


def test_chunker_long_is_windowed():
    words = " ".join(f"w{i}" for i in range(300))
    chunks = chunk_text(words, single_max_words=100, size_words=80, overlap_words=20)
    assert len(chunks) > 1
    assert all(len(c.split()) <= 80 for c in chunks)


# ---- schema validation ------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   \n\t ", "\n\n\n", "has\x00null"])
def test_entry_rejects_bad_content(bad):
    with pytest.raises(ValueError):
        EntryCreate(content=bad)


def test_entry_preserves_whitespace():
    raw = "  tabs\tand\nnewlines  "
    assert EntryCreate(content=raw).content == raw


def test_query_rejects_empty():
    with pytest.raises(ValueError):
        QueryRequest(query="   ")


# ---- storage ----------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"), embed_dim=DIM)
    yield d
    d.close()


def test_db_file_is_locked_down(db, tmp_path):
    mode = os.stat(tmp_path / "t.db").st_mode & 0o777
    assert mode == 0o600


def test_add_and_list_newest_first(db):
    a = db.add_entry("first")
    b = db.add_entry("second")
    entries = db.list_entries()
    assert [e["id"] for e in entries] == [b["id"], a["id"]]
    assert db.count_active_entries() == 2


def test_supersede_then_restore_round_trips_index(db):
    e = db.add_entry("server password is hunter2")
    db.add_chunks(e["id"], [(0, "server password is hunter2", vec(1.0))])
    assert db.knn(vec(1.0), 1)  # findable

    assert db.supersede_entry(e["id"]) is True
    assert db.get_entry(e["id"])["is_superseded"] == 1
    assert db.knn(vec(1.0), 1) == []  # pulled from index
    assert db.count_active_entries() == 0

    db.restore_entry(e["id"])
    assert db.get_entry(e["id"])["is_superseded"] == 0
    assert db.knn(vec(1.0), 1)  # re-indexed without re-embedding


def test_edit_in_place_keeps_id_and_clears_chunks(db):
    # A text edit updates the note in place (same id), then drops its stale chunks
    # so the background indexer can re-embed the new content.
    e = db.add_entry("old text")
    db.add_chunks(e["id"], [(0, "old text", vec(1.0))])
    assert db.knn(vec(1.0), 1)
    updated = db.set_content_in_place(e["id"], "new text")
    assert updated["id"] == e["id"]                 # same note, not a new one
    assert updated["content"] == "new text"
    assert updated["is_superseded"] == 0
    db.clear_chunks(e["id"])
    assert db.knn(vec(1.0), 1) == []                # stale vectors pulled
    assert db.get_chunks(e["id"]) == []             # chunk rows gone too


def test_lexical_search_finds_exact_only_active(db):
    keep = db.add_entry("Reference code REF-0042 here")
    drop = db.add_entry("Reference code REF-0099 here")
    db.supersede_entry(drop["id"])
    hits = db.lexical_search("REF-0042", 10)
    assert [h["id"] for h in hits] == [keep["id"]]
    assert db.lexical_search("REF-0099", 10) == []  # superseded excluded


# ---- retriever degrades to exact search when Ollama is down -----------

class _DeadEmbedder:
    """Stands in for Ollama being unreachable: every embed call raises."""

    async def embed_query(self, text):
        raise RuntimeError("ollama down")

    async def embed_document_one(self, text):
        raise RuntimeError("ollama down")


def test_query_returns_exact_matches_when_embeddings_fail(db):
    # Invariant: exact-text search is instant regardless of Ollama. A semantic failure
    # must never sink the query and discard the lexical hits it already had.
    db.add_entry("my tailscale ip is 100.71.171.89 for my mac mini")
    db.add_entry("tomorrow meeting with david at 3 pm")
    result = asyncio.run(Retriever(db, _DeadEmbedder(), Settings()).query("tailscale"))
    contents = [e["content"] for e in result["entries"]]
    assert any("tailscale" in c for c in contents)


def test_suggest_is_empty_when_embeddings_fail(db):
    # The peek is purely semantic, so Ollama being down yields nothing, never a 500.
    db.add_entry("my tailscale ip is 100.71.171.89 for my mac mini")
    result = asyncio.run(Retriever(db, _DeadEmbedder(), Settings()).suggest("my tailscale is down"))
    assert result == {"match": None, "score": 0.0, "more": 0, "matches": []}


def test_unindexed_active_ids_finds_only_chunkless_active(db):
    # The self-heal pass re-indexes active notes that have no chunks (saved while Ollama
    # was down). Indexed and superseded notes must be excluded.
    a = db.add_entry("saved while ollama was down")
    b = db.add_entry("already indexed")
    db.add_chunks(b["id"], [(0, "already indexed", vec(1.0))])
    c = db.add_entry("superseded note")
    db.supersede_entry(c["id"])
    ids = db.unindexed_active_ids(10)
    assert a["id"] in ids
    assert b["id"] not in ids
    assert c["id"] not in ids


def test_indexer_enqueue_dedupes(db):
    # reconcile re-enqueues backlog every pass, so a note already queued must not pile up.
    idx = Indexer(db, _DeadEmbedder(), Settings())
    idx.enqueue(1)
    idx.enqueue(1)
    idx.enqueue(2)
    assert idx.pending() == 2


def test_supersede_missing_returns_false(db):
    assert db.supersede_entry(999999) is False
    assert db.restore_entry(999999) is None


def test_reset_wipes(db):
    db.add_entry("x")
    db.reset()
    assert db.count_active_entries() == 0
    assert db.list_entries() == []


def test_set_content_in_place_keeps_id_and_vectors(db):
    e = db.add_entry("- [ ] task")
    db.add_chunks(e["id"], [(0, "- [ ] task", vec(1.0))])
    updated = db.set_content_in_place(e["id"], "- [x] task")
    assert updated["id"] == e["id"]                 # same note, not a new one
    assert updated["content"] == "- [x] task"
    assert updated["is_superseded"] == 0
    assert db.knn(vec(1.0), 1)                       # vectors untouched (no re-embed)


def test_set_content_in_place_rejects_superseded(db):
    e = db.add_entry("- [ ] task")
    db.supersede_entry(e["id"])
    assert db.set_content_in_place(e["id"], "- [x] task") is None
    assert db.set_content_in_place(999999, "whatever") is None


# ---- checklist toggling ----------------------------------------------

CHECKLIST = "todo\n- [ ] milk\n- [ ] eggs\n* [x] bread\nnot a box"


def test_set_checkbox_checks_and_unchecks():
    assert set_checkbox(CHECKLIST, 0, True) == "todo\n- [x] milk\n- [ ] eggs\n* [x] bread\nnot a box"
    assert set_checkbox(CHECKLIST, 2, False) == "todo\n- [ ] milk\n- [ ] eggs\n* [ ] bread\nnot a box"


def test_set_checkbox_is_idempotent_and_indexed_top_down():
    # box 2 is already [x]; setting it true again is a no-op rewrite
    assert set_checkbox(CHECKLIST, 2, True) == CHECKLIST
    # only the targeted ordinal changes
    assert set_checkbox(CHECKLIST, 1, True) == "todo\n- [ ] milk\n- [x] eggs\n* [x] bread\nnot a box"


def test_set_checkbox_out_of_range_returns_none():
    assert set_checkbox(CHECKLIST, 9, True) is None
    assert set_checkbox("no checkboxes here", 0, True) is None


def test_checkbox_toggle_schema_rejects_negative_index():
    with pytest.raises(ValueError):
        CheckboxToggle(index=-1, checked=True)


# ---- markdown export rendering ---------------------------------------

def test_render_stream_markdown_format_and_empty():
    assert render_stream_markdown([]) == ""
    out = render_stream_markdown([
        {"created_at": "2026-06-08T01:00:00Z", "content": "first  "},
        {"created_at": "2026-06-08T02:00:00Z", "content": "second"},
    ])
    assert out.startswith("# Scratchpad")
    assert "## 2026-06-08T01:00:00Z" in out
    assert "first\n" in out and "first  \n" not in out  # trailing ws trimmed per note
    assert "second" in out
