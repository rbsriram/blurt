"""Chunking: split an entry into embeddable pieces.

Pure function, no I/O, trivially testable. Short entries stay whole (one chunk
that preserves the original text and formatting). Long entries become a sliding
window so a query can match any region of a long note.
"""

from __future__ import annotations


def chunk_text(
    content: str,
    *,
    single_max_words: int = 100,
    size_words: int = 80,
    overlap_words: int = 20,
) -> list[str]:
    words = content.split()
    if not words:
        return []
    if len(words) <= single_max_words:
        return [content]  # keep the original verbatim, formatting intact

    step = max(1, size_words - overlap_words)
    chunks: list[str] = []
    for start in range(0, len(words), step):
        window = words[start : start + size_words]
        chunks.append(" ".join(window))
        if start + size_words >= len(words):
            break
    return chunks
