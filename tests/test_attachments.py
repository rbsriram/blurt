"""Image attachments: what lands on disk, what can be read back, what gets embedded.

Two properties carry the security of this feature and are pinned here: the file
type comes from the bytes (never the caller's Content-Type), and a served name
must match the generated form (so no user string is ever joined into a path).

The routes are exercised by calling them directly with a stub request. They need
no DB or app state, so a live server would only make these slower and flakier.
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest
from fastapi import HTTPException

from blurt.api import routes
from blurt.config import Settings
from blurt.core import attachments
from blurt.core.chunker import chunk_text
from blurt.core.dateref import anchor_dates
from blurt.core.indexer import Indexer
from blurt.db import Database

DIM = 8

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 16
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<!DOCTYPE html><html><body>hi</body></html>"


# ---- sniffing ----------------------------------------------------------

@pytest.mark.parametrize("data, ext", [(PNG, "png"), (JPEG, "jpg"), (GIF, "gif"), (WEBP, "webp")])
def test_sniff_reads_the_real_type(data, ext):
    assert attachments.sniff_ext(data) == ext


@pytest.mark.parametrize("data", [SVG, HTML, b"just text", b"", b"RIFF\x00\x00\x00\x00AVI "])
def test_sniff_rejects_everything_else(data):
    # SVG especially: it is a scriptable document, not a picture.
    assert attachments.sniff_ext(data) is None


# ---- saving ------------------------------------------------------------

def test_save_writes_the_bytes_and_names_the_file_itself(tmp_path):
    name = attachments.save(PNG, tmp_path / "blurt-files")
    assert attachments.is_valid_name(name)
    written = tmp_path / "blurt-files" / name
    assert written.read_bytes() == PNG
    assert written.stat().st_mode & 0o777 == 0o600


def test_save_ignores_a_lying_content_type(tmp_path):
    # The caller says nothing about the type here; only the bytes decide.
    assert attachments.save(JPEG, tmp_path).endswith(".jpg")
    assert attachments.save(GIF, tmp_path).endswith(".gif")


def test_save_refuses_a_non_image(tmp_path):
    with pytest.raises(ValueError):
        attachments.save(SVG, tmp_path)
    assert not any(tmp_path.iterdir())


def test_names_do_not_collide(tmp_path):
    names = {attachments.save(PNG, tmp_path) for _ in range(20)}
    assert len(names) == 20


def test_generated_names_carry_no_date(tmp_path):
    # Deliberate: a date in the path would be picked up by the date parser and hang
    # a phantom date chip on every note holding an image.
    name = attachments.save(PNG, tmp_path)
    note = f"![the graph]({attachments.markdown_path(name)})"
    assert anchor_dates(note, date(2026, 8, 24), "DMY") == []


# ---- name validation (the path-traversal guard) ------------------------

@pytest.mark.parametrize("name", [
    "../../etc/passwd", "..%2fpasswd", "a/b.png", "/abs.png", "0123456789abcdef.svg",
    "0123456789abcdef.png.exe", "0123456789ABCDEF.png", "short.png", "", ".png",
    "0123456789abcdef.png/../x", "0123456789abcdef\n.png",
])
def test_invalid_names_are_refused(name):
    assert not attachments.is_valid_name(name)


def test_valid_name_round_trips(tmp_path):
    name = attachments.save(WEBP, tmp_path)
    assert attachments.is_valid_name(name)
    assert attachments.media_type(name) == "image/webp"
    assert attachments.markdown_path(name) == f"blurt-files/{name}"


# ---- what the embedder sees -------------------------------------------

def test_text_for_search_keeps_the_caption_and_drops_the_path():
    note = "gate code photo\n![the keypad by the side door](blurt-files/0123456789abcdef.png)"
    out = attachments.text_for_search(note)
    assert out == "gate code photo\nthe keypad by the side door"
    assert "blurt-files" not in out


def test_text_for_search_leaves_ordinary_links_alone():
    note = "see [the docs](https://example.com/x) and https://example.com/y"
    assert attachments.text_for_search(note) == note


def test_referenced_names_finds_only_our_own():
    note = (
        "![a](blurt-files/0123456789abcdef.png)\n"
        "![b](https://example.com/remote.png)\n"
        "![c](blurt-files/../secret.png)"
    )
    assert attachments.referenced_names(note) == ["0123456789abcdef.png"]


# ---- indexing ----------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "t.db"), embed_dim=DIM)
    yield d
    d.close()


class _FakeEmbedder:
    """Ollama stand-in: records what it was asked to embed, returns fixed vectors."""

    def __init__(self):
        self.seen: list[str] = []

    async def embed_documents(self, texts):
        self.seen.extend(texts)
        return [[1.0] + [0.0] * (DIM - 1) for _ in texts]


def test_indexer_embeds_the_caption_not_the_file_path(db):
    entry = db.add_entry("![spare key under the blue flowerpot](blurt-files/0123456789abcdef.png)")
    emb = _FakeEmbedder()
    asyncio.run(Indexer(db, emb, Settings())._index_group([entry["id"]]))
    assert emb.seen == ["spare key under the blue flowerpot"]
    assert db.get_chunks(entry["id"])


def test_image_only_note_still_gets_a_chunk(db):
    # An uncaptioned image strips to nothing, and a note with no chunks is treated
    # as unindexed forever (db.unindexed_active_ids), so the raw content stands in.
    content = "![](blurt-files/0123456789abcdef.png)"
    assert attachments.text_for_search(content).strip() == ""
    entry = db.add_entry(content)
    emb = _FakeEmbedder()
    asyncio.run(Indexer(db, emb, Settings())._index_group([entry["id"]]))
    assert emb.seen == [content]
    assert db.get_chunks(entry["id"])
    assert db.unindexed_active_ids(10) == []


def test_chunker_still_yields_nothing_for_genuinely_empty_text():
    assert chunk_text(attachments.text_for_search("")) == []


# ---- the endpoints -----------------------------------------------------

class _Req:
    """Minimal stand-in for a Starlette Request: headers plus a body."""

    def __init__(self, data: bytes, content_type: str = "image/png", length: int | None = None):
        self.headers = {
            "content-type": content_type,
            "content-length": str(len(data) if length is None else length),
        }
        self._data = data

    async def body(self) -> bytes:
        return self._data


@pytest.fixture
def notes_dir(tmp_path, monkeypatch):
    """Point the routes at a throwaway notes folder (no settings.json => beside the DB)."""
    monkeypatch.setenv("BLURT_DB_PATH", str(tmp_path / "blurt.db"))
    monkeypatch.setattr(routes, "settings", Settings())
    return tmp_path


def test_upload_stores_the_image_and_returns_its_reference(notes_dir):
    out = asyncio.run(routes.upload_file(_Req(PNG)))
    assert out["path"] == f"blurt-files/{out['name']}"
    assert out["url"] == f"/api/files/{out['name']}"
    assert (notes_dir / "blurt-files" / out["name"]).read_bytes() == PNG


def test_upload_trusts_the_bytes_over_the_header(notes_dir):
    # Claims PNG, actually a GIF: the extension follows the bytes.
    out = asyncio.run(routes.upload_file(_Req(GIF, content_type="image/png")))
    assert out["name"].endswith(".gif")


@pytest.mark.parametrize("data, status", [(SVG, 415), (HTML, 415), (b"", 422)])
def test_upload_refuses_what_it_cannot_draw(notes_dir, data, status):
    with pytest.raises(HTTPException) as e:
        asyncio.run(routes.upload_file(_Req(data)))
    assert e.value.status_code == status
    assert not (notes_dir / "blurt-files").exists()


def test_upload_refuses_an_oversize_body(notes_dir, monkeypatch):
    monkeypatch.setenv("BLURT_ATTACHMENT_MAX_BYTES", "64")
    monkeypatch.setattr(routes, "settings", Settings())
    big = PNG + b"\x00" * 200
    with pytest.raises(HTTPException) as e:
        asyncio.run(routes.upload_file(_Req(big)))
    assert e.value.status_code == 413


def test_upload_refuses_on_the_declared_length_before_reading(notes_dir, monkeypatch):
    monkeypatch.setenv("BLURT_ATTACHMENT_MAX_BYTES", "64")
    monkeypatch.setattr(routes, "settings", Settings())

    class _Exploding(_Req):
        async def body(self):
            raise AssertionError("body must not be read once the header is oversize")

    with pytest.raises(HTTPException) as e:
        asyncio.run(routes.upload_file(_Exploding(PNG, length=10_000)))
    assert e.value.status_code == 413


def test_get_file_serves_a_stored_image(notes_dir):
    name = asyncio.run(routes.upload_file(_Req(JPEG)))["name"]
    resp = asyncio.run(routes.get_file(name))
    assert resp.media_type == "image/jpeg"
    assert Path(resp.path) == notes_dir / "blurt-files" / name


@pytest.mark.parametrize("name", ["../../blurt.db", "0123456789abcdef.png", "x.png", "..%2f..%2fblurt.db"])
def test_get_file_404s_on_anything_unknown_or_escaping(notes_dir, name):
    # Includes a well-formed name that simply is not there: same 404, no leak.
    with pytest.raises(HTTPException) as e:
        asyncio.run(routes.get_file(name))
    assert e.value.status_code == 404


# ---- moving the notes folder ------------------------------------------

def test_moving_the_notes_folder_takes_the_images_along(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    (old / "blurt-files").mkdir(parents=True)
    (old / "blurt-files" / "0123456789abcdef.png").write_bytes(PNG)
    new.mkdir()
    routes._move_attachments(old, new)
    assert (new / "blurt-files" / "0123456789abcdef.png").read_bytes() == PNG
    assert not (old / "blurt-files").exists()


def test_moving_into_a_folder_that_already_has_images_merges(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    (old / "blurt-files").mkdir(parents=True)
    (new / "blurt-files").mkdir(parents=True)
    (old / "blurt-files" / "aaaaaaaaaaaaaaaa.png").write_bytes(PNG)
    (new / "blurt-files" / "bbbbbbbbbbbbbbbb.png").write_bytes(GIF)
    routes._move_attachments(old, new)
    assert sorted(p.name for p in (new / "blurt-files").iterdir()) == [
        "aaaaaaaaaaaaaaaa.png", "bbbbbbbbbbbbbbbb.png",
    ]


def test_moving_when_there_is_nothing_to_move_is_a_no_op(tmp_path):
    routes._move_attachments(tmp_path / "old", tmp_path / "new")
    assert not (tmp_path / "new").exists()
