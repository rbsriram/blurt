"""MediaStore: the local image store behind pasted screenshots. The properties that
matter are that it accepts only real images, dedupes by content, and can never be
talked into reading or writing outside its own directory."""

from __future__ import annotations

import pytest

from blurt.core.media import MediaStore

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 24


@pytest.fixture
def store(tmp_path):
    return MediaStore(tmp_path / "media")


def test_stores_each_image_kind(store):
    for data, ext in [(PNG, "png"), (JPG, "jpg"), (GIF, "gif"), (WEBP, "webp")]:
        name = store.save(data)
        assert name.endswith(f".{ext}")
        resolved = store.resolve(name)
        assert resolved is not None
        path, media_type = resolved
        assert path.read_bytes() == data
        assert media_type.startswith("image/")


def test_dir_and_files_are_owner_only(store, tmp_path):
    store.save(PNG)
    media = tmp_path / "media"
    assert (media.stat().st_mode & 0o777) == 0o700
    f = next(media.iterdir())
    assert (f.stat().st_mode & 0o777) == 0o600


def test_content_addressed_dedup(store):
    a = store.save(PNG)
    b = store.save(PNG)
    assert a == b  # identical bytes, one name
    assert sum(1 for _ in (store._dir).iterdir()) == 1  # written once


def test_rejects_non_image(store):
    with pytest.raises(ValueError):
        store.save(b"this is not an image at all")


def test_rejects_riff_that_is_not_webp(store):
    avi = b"RIFF" + b"\x00\x00\x00\x00" + b"AVI " + b"\x00" * 24
    with pytest.raises(ValueError):
        store.save(avi)


def test_rejects_empty_and_oversize(store):
    from blurt.core.media import MAX_BYTES

    with pytest.raises(ValueError):
        store.save(b"")
    with pytest.raises(ValueError):
        store.save(PNG[:8] + b"\x00" * (MAX_BYTES + 1))


def test_resolve_blocks_traversal_and_garbage(store):
    store.save(PNG)
    for bad in ["../secret", "..%2f..%2fetc%2fpasswd", "foo.png", "a" * 64 + ".exe",
                "/etc/passwd", "abc.png", ""]:
        assert store.resolve(bad) is None


def test_resolve_missing_returns_none(store):
    assert store.resolve("a" * 64 + ".png") is None  # well-formed name, no such file
