"""Image attachments: bytes on disk, a Markdown reference in the note.

The stream stays text. Pasting a screenshot writes the image into `blurt-files/`
next to `scratchpad.md` and puts a plain Markdown image reference in the note's
content, so every invariant the rest of the app leans on survives untouched:
content is still verbatim text, the mirror is still a readable Markdown file
(with relative links that resolve in any editor), and edit/supersede/sync need
no special case for images.

Two rules make that safe. Nothing the client sends decides what lands on disk:
the type is sniffed from the bytes themselves (a lying `Content-Type` cannot
smuggle an .html or .svg past it) and the name is generated here. And nothing
outside the folder can be read back: a served name must match `_NAME_RE`, so a
filesystem path is never joined from unvalidated input.

Names are random hex, deliberately dateless: a `2026-08-24` in a path would be
picked up by the date parser and hang a phantom date chip on the note.
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path

# Sits inside the user's notes folder, beside scratchpad.md, so the relative
# links in the mirror resolve for Obsidian/Typora/anything else reading it.
ATTACH_DIRNAME = "blurt-files"

# Rendered/served extensions. Kept to what every browser draws natively; SVG is
# excluded on purpose (it is a script-bearing document, not an image).
_MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}

SUPPORTED = "PNG, JPEG, GIF or WebP"

_NAME_RE = re.compile(r"^[0-9a-f]{16}\.(png|jpg|gif|webp)$")

# `![alt](path)` in note text. Alt may be empty; the path may not contain spaces.
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def sniff_ext(data: bytes) -> str | None:
    """The real file type, read from the bytes. None if it is not an image we serve."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def is_valid_name(name: str) -> bool:
    """Whether `name` is one of ours. Everything served goes through this first, so
    `..`, slashes, and unexpected extensions can never reach a path join."""
    return bool(_NAME_RE.match(name))


def media_type(name: str) -> str:
    return _MEDIA_TYPES[name.rsplit(".", 1)[1]]


def attachments_dir(notes_dir: Path) -> Path:
    return Path(notes_dir) / ATTACH_DIRNAME


def markdown_path(name: str) -> str:
    """What goes in the note: a relative path, so the mirror stays portable."""
    return f"{ATTACH_DIRNAME}/{name}"


def save(data: bytes, folder: Path) -> str:
    """Write image bytes into `folder` under a generated name and return the name.

    Raises ValueError if the bytes are not a supported image.
    """
    ext = sniff_ext(data)
    if ext is None:
        raise ValueError(f"not a supported image ({SUPPORTED})")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(8)}.{ext}"
    path = folder / name
    # Same-dir temp + rename: a reader (the UI fetching it immediately) sees the
    # whole file or nothing, never a half-written one.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    path.chmod(0o600)
    return name


def text_for_search(content: str) -> str:
    """The note as the embedder should see it: image references reduced to their
    caption. The path is noise that would otherwise dilute the vector (and match
    "files" in exact search), while the caption is the only thing about an image
    that search can actually work with."""
    return IMAGE_RE.sub(lambda m: m.group(1), content)


def referenced_names(content: str) -> list[str]:
    """The attachment names a note refers to (ours only, in order)."""
    out = []
    for _, path in IMAGE_RE.findall(content):
        prefix = f"{ATTACH_DIRNAME}/"
        if path.startswith(prefix) and is_valid_name(path[len(prefix):]):
            out.append(path[len(prefix):])
    return out
