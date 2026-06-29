"""Local store for pasted images. Files live beside the DB, named by content hash
so identical pastes dedupe and the name can never carry a path. Nothing here ever
leaves the machine: images are served back only over the localhost API.

OCR is deliberately not done yet (see DECISIONS): a pasted image is viewable but not
semantically searchable. That is the next phase, gated on a local model choice."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

# Sniffed from the first bytes, not the client's content-type (which is spoofable).
# Maps a magic-byte signature to the extension we store the file under.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"RIFF", "webp"),  # RIFF....WEBP; the WEBP tag is checked below
)

# A stored name is exactly <64-hex sha256>.<ext>. Anything else is rejected before
# touching the filesystem, so a request can never escape the media dir.
_NAME_RE = re.compile(r"^[a-f0-9]{64}\.(png|jpg|gif|webp)$")

# Generous for a screenshot, small enough that a stray paste cannot fill the disk.
MAX_BYTES = 20 * 1024 * 1024

_MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


def _sniff_ext(data: bytes) -> str | None:
    """The image extension implied by the bytes, or None if it is not an image we
    accept. Guards against a non-image (or a disguised file) being stored."""
    for sig, ext in _SIGNATURES:
        if data.startswith(sig):
            if ext == "webp":
                return "webp" if data[8:12] == b"WEBP" else None
            return ext
    return None


class MediaStore:
    def __init__(self, directory: Path):
        self._dir = Path(directory)

    def _ensure_dir(self) -> None:
        # Created lazily on first paste, owner-only like the DB. mkdir is a no-op if
        # it exists; chmod re-asserts the mode each time, cheaply.
        self._dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._dir, 0o700)

    def save(self, data: bytes) -> str:
        """Validate, store owner-only, and return the content-addressed file name.
        Raises ValueError if the bytes are not an accepted image or are too large."""
        if not data:
            raise ValueError("empty upload")
        if len(data) > MAX_BYTES:
            raise ValueError("image too large")
        ext = _sniff_ext(data)
        if ext is None:
            raise ValueError("not a supported image")
        name = f"{hashlib.sha256(data).hexdigest()}.{ext}"
        self._ensure_dir()
        path = self._dir / name
        if not path.exists():  # content-addressed: identical bytes are written once
            path.write_bytes(data)
            os.chmod(path, 0o600)
        return name

    def resolve(self, name: str) -> tuple[Path, str] | None:
        """Map a requested name to its on-disk path and media type, or None if the
        name is malformed or the file is absent. The regex blocks path traversal."""
        if not _NAME_RE.match(name):
            return None
        path = self._dir / name
        if not path.is_file():
            return None
        return path, _MEDIA_TYPES[name.rsplit(".", 1)[1]]
