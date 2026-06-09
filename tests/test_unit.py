"""Offline unit tests: no Ollama, no server, fast. These run in CI.

The full behavioral contract lives in docs/test_suite.py (integration, needs a
live server + Ollama). These cover the pure logic and the storage layer so
regressions are caught without external services.
"""

import asyncio
import os
from datetime import date

import pytest

from blurt.api.schemas import CheckboxToggle, EntryCreate, QueryRequest
from blurt.config import Settings
from blurt.core.checklist import set_checkbox
from blurt.core.chunker import chunk_text
from blurt.core.dateref import anchor_dates, query_is_date_only, query_ranges
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


# ---- date references (dateref) ----------------------------------------

# Wednesday, anchor for every relative case below.
TODAY = date(2026, 6, 10)


@pytest.mark.parametrize("text, expected", [
    ("meeting with David tomorrow", ["2026-06-11"]),
    ("call mom today", ["2026-06-10"]),
    ("that was yesterday", ["2026-06-09"]),
    ("ship it day after tomorrow", ["2026-06-12"]),     # not 06-11 from "tomorrow"
    ("dentist next friday", ["2026-06-19"]),            # Fri after this week's Fri
    ("standup this monday", ["2026-06-08"]),            # Monday of the current week
    ("flight on 2026-07-01", ["2026-07-01"]),
    ("invoice due Jun 15", ["2026-06-15"]),
    ("party 4 july", ["2026-07-04"]),
    ("see you on the 14th of June", ["2026-06-14"]),    # "of" between day and month
    ("on the 14th of this month i fly", ["2026-06-14"]),  # not "jun 1" from "this month"
    ("dated 14/2/2024", ["2024-02-14"]),                # day-first slash date with year
    ("dated 14-12-2026", ["2026-12-14"]),               # day-first dash date with year
    ("review in 3 days", ["2026-06-13"]),
    ("started 2 weeks ago", ["2026-05-27"]),
])
def test_anchor_dates_resolves(text, expected):
    assert anchor_dates(text, TODAY) == expected


@pytest.mark.parametrize("text", [
    "do taxes this month",          # a whole month is not a single day: no chip...
    "lunch next week",              # ...nor is a whole week.
])
def test_vague_spans_do_not_anchor_a_note(text):
    assert anchor_dates(text, TODAY) == []


def test_numeric_date_disambiguates_when_it_can():
    # 14 can't be a month, so it's the day regardless of the chosen order.
    assert anchor_dates("14-12-2026", TODAY) == ["2026-12-14"]
    assert anchor_dates("14-12-2026", TODAY, "MDY") == ["2026-12-14"]
    assert anchor_dates("12-12-26", TODAY) == ["2026-12-12"]      # 2-digit year


def test_ambiguous_numeric_date_follows_chosen_order():
    # 6/4: only the format preference can decide. Day-first vs month-first.
    assert anchor_dates("6/4/2026", TODAY, "DMY") == ["2026-04-06"]
    assert anchor_dates("6/4/2026", TODAY, "MDY") == ["2026-06-04"]


@pytest.mark.parametrize("text", [
    "june 1", "jun 1", "june1", "jun1", "1 june", "1june", "1st jun", "1st june", "jun 1st",
])
def test_month_name_day_formats_all_resolve(text):
    # Spaced, glued, abbreviated, ordinal: all read as June 1 of the current year.
    assert anchor_dates(text, TODAY) == ["2026-06-01"]


def test_month_day_without_year_is_current_year_not_next():
    # June 1 is before TODAY (Jun 10); it should stay this year, findable by search,
    # not jump to next year.
    assert anchor_dates("jun 1", TODAY) == ["2026-06-01"]
    assert query_ranges("jun 1", TODAY) == [("2026-06-01", "2026-06-01")]


@pytest.mark.parametrize("text", ["tomorrow", "2nd feb", "on 14/2/2024", "next week", "due jun 1"])
def test_pure_date_queries_are_recognized(text):
    assert query_is_date_only(text, TODAY) is True


@pytest.mark.parametrize("text", ["meeting tomorrow", "sarah birthday", "dog", "pay rent next week now"])
def test_queries_with_a_topic_are_not_date_only(text):
    assert query_is_date_only(text, TODAY) is False


def test_pure_date_search_drops_semantic_noise(db):
    # "tomorrow" must not surface a note dated today via fuzzy meaning. With a dead
    # embedder, semantic is off anyway; the point is date+lexical still return and the
    # today-note (no text/date match) does not.
    tom = db.add_entry("dentist appt")
    db.set_entry_dates(tom["id"], ["2026-06-11"])           # tomorrow, relative to TODAY
    today = db.add_entry("vish bday")
    db.set_entry_dates(today["id"], ["2026-06-10"])         # today: must NOT match "tomorrow"
    res = asyncio.run(Retriever(db, _DeadEmbedder(), Settings()).query("11/6/2026"))
    ids = [e["id"] for e in res["entries"]]
    assert tom["id"] in ids and today["id"] not in ids


@pytest.mark.parametrize("text", [
    "meeting David at five",        # bare number is not a date (precision over recall)
    "we may go there",              # bare month word without a day number
    "see you at 9pm",               # time of day is left to the verbatim text
    "buy 6 eggs",
    "ref 1/6 attached",             # slash with no year: a ref, not a date
    "use 3/4 cup of flour",         # fraction, not a date
])
def test_anchor_dates_ignores_ambiguous(text):
    assert anchor_dates(text, TODAY) == []


def test_anchor_dates_dedupes_and_sorts():
    assert anchor_dates("today and again today, then tomorrow", TODAY) == ["2026-06-10", "2026-06-11"]


def test_query_ranges_expands_week_to_span():
    assert query_ranges("next week", TODAY) == [("2026-06-15", "2026-06-21")]


def test_query_ranges_point_is_single_day():
    assert query_ranges("tomorrow", TODAY) == [("2026-06-11", "2026-06-11")]


# ---- date storage + search -------------------------------------------

def test_entry_dates_frozen_on_save_and_searchable(db):
    e = db.add_entry("meeting David tomorrow")
    db.set_entry_dates(e["id"], anchor_dates("meeting David tomorrow", TODAY))
    assert db.get_entry(e["id"])["dates"] == ["2026-06-11"]
    # The "next week" range (Mon 15..Sun 21) must NOT include tomorrow (Thu 11).
    assert db.entries_in_ranges([("2026-06-15", "2026-06-21")], 10) == []
    # A range covering tomorrow finds it.
    hits = db.entries_in_ranges([("2026-06-11", "2026-06-11")], 10)
    assert [h["id"] for h in hits] == [e["id"]]


def test_date_search_excludes_superseded(db):
    e = db.add_entry("dentist tomorrow")
    db.set_entry_dates(e["id"], ["2026-06-11"])
    db.supersede_entry(e["id"])
    assert db.entries_in_ranges([("2026-06-11", "2026-06-11")], 10) == []


def test_set_entry_dates_replaces(db):
    e = db.add_entry("note")
    db.set_entry_dates(e["id"], ["2026-06-11", "2026-06-12"])
    db.set_entry_dates(e["id"], ["2026-06-20"])          # re-save drops the old set
    assert db.get_entry(e["id"])["dates"] == ["2026-06-20"]


def test_backfill_anchors_to_each_notes_creation_date(db):
    # A pre-existing note's "tomorrow" must resolve against when it was WRITTEN,
    # not against today, and the pass must be one-shot (idempotent).
    e = db.add_entry("call David tomorrow")
    db._conn.execute("UPDATE entries SET created_at = ? WHERE id = ?", ("2026-01-01T08:00:00.000Z", e["id"]))
    db._conn.commit()
    processed = db.backfill_dates(anchor_dates)
    assert processed == 1
    assert db.get_entry(e["id"])["dates"] == ["2026-01-02"]   # day after creation, not today
    assert db.backfill_dates(anchor_dates) == 0               # flag set: never runs twice


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
