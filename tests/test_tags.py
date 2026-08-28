"""Project tags: what counts as a #tag and how the lens list is built.

Two properties are pinned here. Extraction: a tag opens a word and starts with a
letter, so numbers, URL fragments, headings, and code never sprout phantom
projects. Aggregation: the list is derived from active note text at read time
(most-used first), because a tag is a lens over the stream, never structure.
"""

from __future__ import annotations

import pytest

from blurt.core.tags import collect_tags, entries_for_tag, extract_tags

# ---- extraction --------------------------------------------------------


@pytest.mark.parametrize(
    "content, tags",
    [
        ("ship the #zovery deck tomorrow", ["zovery"]),
        ("#blurt sync notes", ["blurt"]),
        ("two in one: #blurt needs the #zovery treatment", ["blurt", "zovery"]),
        ("hyphens and digits ride along #q3-plan #v2", ["q3-plan", "v2"]),
    ],
)
def test_extracts_tags(content, tags):
    assert extract_tags(content) == tags


@pytest.mark.parametrize(
    "content",
    [
        "issue #42 is not a project",              # must start with a letter
        "compilers: c#stuff is mid-word",          # must open a word
        "# a heading line",                        # "# " never matches
        "## subheading",                           # "##" never matches
        "see https://example.com/page#top",        # URL fragment is the URL's
        "see www.example.com/a#frag too",          # same for bare www URLs
        "code keeps it literal: `#notatag`",       # code span
        "```\n#build notes\n```",                  # fenced block
        "![#caption](blurt-files/0011223344556677.png)",  # image/link targets
    ],
)
def test_ignores_non_tags(content):
    assert extract_tags(content) == []


def test_distinct_case_insensitive_within_note():
    assert extract_tags("#Zovery then #zovery again") == ["Zovery"]


# ---- aggregation -------------------------------------------------------


def _entry(content):
    return {"content": content}


def test_collects_most_recently_used_first():
    # Entries arrive newest-first (stream order). A project with more notes does
    # NOT outrank one just touched: saving a note bumps its project to the top.
    entries = [
        _entry("#blurt tags design"),
        _entry("#zovery call notes"),
        _entry("#zovery follow-ups"),
    ]
    assert collect_tags(entries) == [
        {"tag": "blurt", "count": 1},
        {"tag": "zovery", "count": 2},
    ]


def test_one_row_per_tag_however_many_notes():
    entries = [_entry("#thesis a"), _entry("#thesis b"), _entry("#thesis c")]
    assert collect_tags(entries) == [{"tag": "thesis", "count": 3}]


def test_display_casing_is_most_recent():
    entries = [_entry("#Zovery deck"), _entry("#zovery kickoff")]
    assert collect_tags(entries) == [{"tag": "Zovery", "count": 2}]


def test_counts_notes_not_occurrences():
    entries = [_entry("#blurt and #blurt again in one note")]
    assert collect_tags(entries) == [{"tag": "blurt", "count": 1}]


def test_empty_stream_yields_no_tags():
    assert collect_tags([]) == []


# ---- strict membership (the "read together" set) ------------------------


def test_entries_for_tag_is_exact_not_prefix():
    entries = [_entry("#blurty is a different project"), _entry("#blurt ship it")]
    assert [e["content"] for e in entries_for_tag(entries, "blurt")] == ["#blurt ship it"]


def test_entries_for_tag_case_insensitive_and_hash_tolerant():
    entries = [_entry("#Zovery kickoff")]
    assert entries_for_tag(entries, "#zovery") == entries


def test_entries_for_tag_preserves_order():
    entries = [_entry("#c first"), _entry("no tag"), _entry("#c third")]
    assert [e["content"] for e in entries_for_tag(entries, "c")] == ["#c first", "#c third"]
