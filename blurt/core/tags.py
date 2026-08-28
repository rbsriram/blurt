"""Project tags: a `#word` written in a note is a lens, not a folder.

Blurt has no folders and no tag column, on purpose: content is verbatim text and
everything else is derived (the same invariant chunks, date chips, and the mirror
lean on). A tag is therefore just a `#word` the owner typed. This module derives
the tag view from content at read time; nothing about a tag exists in the schema,
so the mirror, sync event log, and export all carry tags for free.

A tag starts with a letter so bare issue/line numbers ("#42") stay plain text, and
it must open a word (start of text or after whitespace) so URL fragments, `##`
headings, and mid-word hashes never match. Code spans, fenced blocks, URLs, and
image/link targets are stripped before scanning, mirroring what the renderer
treats as non-prose.
"""

from __future__ import annotations

import re

TAG_RE = re.compile(r"(?:(?<=\s)|^)#([A-Za-z][\w-]*)")

_CODE_FENCE = re.compile(r"```.*?```", re.S)
_CODE_SPAN = re.compile(r"`[^`]*`")
_IMAGE_OR_LINK = re.compile(r"!?\[[^\]]*\]\([^)\s]+\)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)


def extract_tags(content: str) -> list[str]:
    """Distinct tags in one note, in order of first appearance, `#` stripped.

    Case is preserved for display; distinctness is case-insensitive so "#Zovery"
    and "#zovery" are one tag (first spelling seen wins for this note).
    """
    text = _CODE_FENCE.sub(" ", content)
    text = _CODE_SPAN.sub(" ", text)
    text = _IMAGE_OR_LINK.sub(" ", text)
    text = _URL.sub(" ", text)
    seen: set[str] = set()
    out: list[str] = []
    for m in TAG_RE.finditer(text):
        key = m.group(1).lower()
        if key not in seen:
            seen.add(key)
            out.append(m.group(1))
    return out


def entries_for_tag(entries: list[dict], tag: str) -> list[dict]:
    """The entries whose text carries exactly #tag, preserving input order.

    Strict on purpose: this backs the "read together" view, where #blurt must
    never pull in #blurty. Exact-substring search stays the loose, fast path;
    this is the curated one. Case-insensitive like everything else about tags.
    """
    key = tag.lstrip("#").lower()
    return [e for e in entries if key in (t.lower() for t in extract_tags(e["content"]))]


def collect_tags(entries: list[dict]) -> list[dict]:
    """Aggregate tags across entries (expected newest-first, active, non-secret).

    Returns [{"tag", "count"}] with count = how many notes use the tag, ordered by
    most recent use: saving a note bumps its project to the top, the way a chat app
    surfaces the conversation you just touched. One row per tag, however many notes
    carry it. Display casing is the most recent spelling, which lets the owner
    drift a tag's casing without splitting it.
    """
    counts: dict[str, dict] = {}
    for e in entries:
        for t in extract_tags(e["content"]):
            key = t.lower()
            c = counts.setdefault(key, {"tag": t, "count": 0})   # first sight = most recent use
            c["count"] += 1
    return [{"tag": c["tag"], "count": c["count"]} for c in counts.values()]
