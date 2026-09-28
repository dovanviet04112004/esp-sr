"""The model slot image: the header of storage_format.h, entries aligned and hashed, the slot size respected."""

from __future__ import annotations

import hashlib

import pytest

from srpipe.export import pack_models
from srpipe.generated import grid


def entries_of(image: bytes) -> list[tuple[bytes, int, int, bytes, int]]:
    magic, version, count, grid_hash = pack_models.HEAD.unpack_from(image, 0)
    assert (magic, version, grid_hash) == (pack_models.MAGIC, pack_models.FORMAT_VER, grid.GRID_HASH)
    rows = []
    for i in range(count):
        name, offset, size, sha, kind, _ = pack_models.ENTRY.unpack_from(image, pack_models.HEAD.size + i * 64)
        rows.append((name.rstrip(b"\0"), offset, size, sha, kind))
    return rows


def test_the_header_fits_the_layout_storage_format_fixes() -> None:
    assert pack_models.HEAD.size == 64 and pack_models.ENTRY.size == 64
    assert pack_models.HEAD.size + pack_models.MAX_ENTRIES * pack_models.ENTRY.size <= pack_models.HEADER_BYTES
    assert pack_models.MAGIC.to_bytes(4, "little") == b"SRMD"
    assert pack_models.slot_bytes() == 0x300000


def test_entries_are_aligned_hashed_and_in_order() -> None:
    parts = [pack_models.Entry("wake", "espdl", bytes(range(100))), pack_models.Entry("wake", "norm", b"\x07" * 40)]
    image = pack_models.pack(parts)
    rows = entries_of(image)
    assert [r[0] for r in rows] == [b"wake", b"wake"]
    assert [r[4] for r in rows] == [pack_models.KINDS["espdl"], pack_models.KINDS["norm"]]
    for (_, offset, size, sha, _), part in zip(rows, parts, strict=True):
        assert offset % pack_models.ALIGN_BYTES == 0 and offset >= pack_models.HEADER_BYTES
        assert image[offset : offset + size] == part.data
        assert sha == hashlib.sha256(part.data).digest()


def test_a_flipped_byte_no_longer_matches_its_sha256() -> None:
    image = bytearray(pack_models.pack([pack_models.Entry("probe", "espdl", b"abc" * 50)]))
    (_, offset, size, sha, _) = entries_of(bytes(image))[0]
    image[offset + 3] ^= 1
    assert hashlib.sha256(bytes(image[offset : offset + size])).digest() != sha


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [pack_models.Entry("x" * 17, "espdl", b"1")],
        [pack_models.Entry("wake", "tflite", b"1")],
        [pack_models.Entry("wake", "espdl", b"")],
        [pack_models.Entry("big", "espdl", bytes(0x300000))],
    ],
)
def test_bad_entries_are_refused(entries) -> None:
    with pytest.raises(ValueError):
        pack_models.pack(entries)
