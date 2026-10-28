"""The model slot image: the header of storage_format.h, entries aligned and hashed, the slot size respected; the unit
test table keeps the product's settings partitions where the product reads them."""

from __future__ import annotations

import hashlib
import json

import pytest

from srpipe.export import pack_models
from srpipe.generated import grid, listen, speaker


def entries_of(image: bytes) -> list[tuple[bytes, int, int, bytes, int]]:
    magic, version, count, grid_hash, *_ = pack_models.HEAD.unpack_from(image, 0)
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
    assert pack_models.slot_bytes() == 0xB00000


def rows_of(table) -> dict[str, tuple[int, int]]:
    with table.open(encoding="utf-8") as f:
        lines = [line.split("#")[0] for line in f]
    rows = [[c.strip() for c in line.split(",")] for line in lines if line.strip()]
    return {r[0]: (int(r[3], 0), int(r[4], 0)) for r in rows}


def test_the_unit_table_keeps_the_products_settings_in_place_and_lays_apps_and_models_in_the_same_span() -> None:
    product, unit = rows_of(pack_models.PARTITIONS), rows_of(pack_models.UNIT_PARTITIONS)
    for kept in ("nvs", "otadata", "phy_init", "nvs_keys", "storage", "coredump"):
        assert unit.pop(kept) == product.pop(kept), kept
    span = min(start for start, _ in product.values()), max(start + size for start, size in product.values())
    assert set(product) == {"ota_0", "models_0"} and {"ota_0", "models_0", "models_1", "voice"} == set(unit)
    assert span == (min(start for start, _ in unit.values()), max(start + size for start, size in unit.values()))
    assert pack_models.partition_bytes("models_1", pack_models.UNIT_PARTITIONS) == unit["models_1"][1]


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


def test_the_header_carries_its_commands_listen_hash_and_thresholds_and_0_without_them() -> None:
    entries = [pack_models.Entry("command_ctc", "espdl", b"g" * 10)]
    command = pack_models.pack(entries, listen_hash=listen.HASH, thresholds=(420, 35, 60))
    assert pack_models.HEAD.unpack_from(command, 0)[4:10] == (listen.HASH, 420, 35, 60, 0, 0)
    bare = pack_models.pack([pack_models.Entry("wake", "espdl", b"w")])
    assert not any(pack_models.HEAD.unpack_from(bare, 0)[4:])


def test_the_header_carries_its_speakers_hash_a_threshold_a_count_its_cohorts_top_and_an_entry_its_flags() -> None:
    entries = [pack_models.Entry("speaker", "espdl", b"s" * 10, pack_models.IN_FLASH)]
    table = tuple(-37 + 10 * n for n in range(pack_models.SPEAKERS))
    image = pack_models.pack(entries, speaker_hash=speaker.HASH, speaker_milli_z=table, speaker_cohort_top=100)
    assert pack_models.HEAD.unpack_from(image, 0)[9:] == (speaker.HASH, *table, 100)
    assert pack_models.ENTRY.unpack_from(image, pack_models.HEAD.size)[5] == pack_models.IN_FLASH
    with pytest.raises(ValueError, match="speaker thresholds"):
        pack_models.pack(entries, speaker_milli_z=(*table, 0))
    with pytest.raises(ValueError, match="cohort top"):
        pack_models.pack(entries, speaker_cohort_top=0x10000)


NOISE_AT = pack_models.FIELDS_BYTES + pack_models.MAX_ENTRIES * pack_models.ENTRY.size
EDGES = (3.75, 17.5, 174.0)
NOISY = tuple(tuple(1700 + 40 * n + 300 * b for b in range(len(EDGES))) for n in range(pack_models.SPEAKERS))


def test_the_header_carries_the_noise_edges_and_thresholds_after_every_entry_and_0_without() -> None:
    entries = [pack_models.Entry("speaker", "espdl", b"s" * 10)]
    image = pack_models.pack(entries, speaker_noise_edges=EDGES, speaker_noisy_milli_z=NOISY)
    assert NOISE_AT == 0x240
    got = pack_models.NOISE.unpack_from(image, NOISE_AT)
    assert got[: len(EDGES)] == EDGES and got[len(EDGES) :] == tuple(t for row in NOISY for t in row)
    bare = pack_models.pack(entries)
    assert not any(pack_models.NOISE.unpack_from(bare, NOISE_AT))


@pytest.mark.parametrize(
    ("edges", "noisy", "why"),
    [
        (EDGES, (), "together"),
        (EDGES[:2], NOISY, "rising"),
        ((17.5, 3.75, 174.0), NOISY, "rising"),
        ((-1.0, 3.75, 174.0), NOISY, "rising"),
        ((float("nan"), 3.75, 174.0), NOISY, "rising"),
        (EDGES, NOISY[:-1], "rows"),
        (EDGES, ((40000, 0, 0), *NOISY[1:]), "int16"),
    ],
)
def test_noise_fields_the_chip_would_misread_are_refused(edges, noisy, why) -> None:
    with pytest.raises(ValueError, match=why):
        pack_models.pack(
            [pack_models.Entry("speaker", "espdl", b"s")], speaker_noise_edges=edges, speaker_noisy_milli_z=noisy
        )


def test_the_lock_gives_its_command_rows_thresholds_0_for_one_it_lacks_and_refuses_two(tmp_path, monkeypatch) -> None:
    (tmp_path / "command").mkdir()
    (tmp_path / "command" / "net.espdl").write_bytes(b"n")
    files = [{"file": "net.espdl", "entry": "command_ctc", "kind": "espdl", "sha256": hashlib.sha256(b"n").hexdigest()}]
    row = {
        "files": files,
        "listen_hash": f"0x{listen.HASH:08x}",
        "thresholds": {"reject_permille": 420, "margin_permille": 35, "syllable_permille": 60},
    }
    lock = tmp_path / "models.lock.json"
    monkeypatch.setattr(pack_models, "LOCK", lock)
    monkeypatch.setattr(pack_models, "MODELS", tmp_path)
    lock.write_text(json.dumps({"models": {"command": row}}), encoding="utf-8")
    got = pack_models.locked()
    assert (got.listen_hash, got.thresholds, got.speaker_hash) == (listen.HASH, (420, 35, 60), 0)
    pair = row | {"thresholds": {"reject_permille": 420, "margin_permille": 35}}
    lock.write_text(json.dumps({"models": {"command": pair}}), encoding="utf-8")
    assert pack_models.locked().thresholds == (420, 35, 0)
    lock.write_text(json.dumps({"models": {"command": row, "other": row | {"files": []}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="one command's"):
        pack_models.locked()


def test_the_lock_flags_a_speaker_left_in_flash_and_gives_its_hash_and_threshold(tmp_path, monkeypatch) -> None:
    (tmp_path / "speaker").mkdir()
    (tmp_path / "speaker" / "speaker.espdl").write_bytes(b"b0")
    (tmp_path / "speaker" / "cohort.bin").write_bytes(b"rows")
    files = [
        {"file": "speaker.espdl", "entry": "speaker", "kind": "espdl", "sha256": hashlib.sha256(b"b0").hexdigest()},
        {
            "file": "cohort.bin",
            "entry": "speaker_cohort",
            "kind": "cohort",
            "sha256": hashlib.sha256(b"rows").hexdigest(),
        },
    ]
    row = {
        "files": files,
        "speaker_hash": f"0x{speaker.HASH:08x}",
        "threshold": {"milli_z": [412 + n for n in range(pack_models.SPEAKERS)], "impostors_passing": [0.01] * 8},
        "cohort": {"seed": 1, "stream": 3, "speakers": 27, "rows": 380, "top": 100},
        "weights_in_flash": True,
    }
    lock = tmp_path / "models.lock.json"
    monkeypatch.setattr(pack_models, "LOCK", lock)
    monkeypatch.setattr(pack_models, "MODELS", tmp_path)
    lock.write_text(json.dumps({"models": {"speaker": row}}), encoding="utf-8")
    got = pack_models.locked()
    assert (got.speaker_hash, got.speaker_milli_z) == (speaker.HASH, tuple(412 + n for n in range(8)))
    assert got.speaker_cohort_top == 100
    assert (got.speaker_noise_edges, got.speaker_noisy_milli_z) == ((), ())
    noisy = {"edges": list(EDGES), "milli_z": [list(r) for r in NOISY]}
    with_noise = row | {"threshold": row["threshold"] | {"noise": noisy}}
    lock.write_text(json.dumps({"models": {"speaker": with_noise}}), encoding="utf-8")
    got_noise = pack_models.locked()
    assert (got_noise.speaker_noise_edges, got_noise.speaker_noisy_milli_z) == (EDGES, NOISY)
    lock.write_text(json.dumps({"models": {"speaker": row}}), encoding="utf-8")
    assert [e.flags for e in got.entries] == [pack_models.IN_FLASH, pack_models.IN_FLASH]
    lock.write_text(json.dumps({"models": {"speaker": row | {"weights_in_flash": False}}}), encoding="utf-8")
    assert [e.flags for e in pack_models.locked().entries] == [0, pack_models.IN_FLASH]
    lock.write_text(json.dumps({"models": {"speaker": row, "other": row | {"files": []}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="a speaker"):
        pack_models.locked()
    one = row | {"threshold": {"milli_z": 412, "impostors_passing": 0.01}}
    lock.write_text(json.dumps({"models": {"speaker": one}}), encoding="utf-8")
    with pytest.raises(ValueError, match="speaker-deploy again"):
        pack_models.locked()
    cosine = row | {"threshold": {"permille": [412] * pack_models.SPEAKERS, "impostors_passing": [0.01] * 8}}
    lock.write_text(json.dumps({"models": {"speaker": cosine}}), encoding="utf-8")
    with pytest.raises(ValueError, match="AS-norm score"):
        pack_models.locked()
    z_norm = row | {"cohort": {"seed": 1, "stream": 3, "speakers": 27, "rows": 380}}
    lock.write_text(json.dumps({"models": {"speaker": z_norm}}), encoding="utf-8")
    with pytest.raises(ValueError, match="cohort top None"):
        pack_models.locked()


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
        [pack_models.Entry("big", "espdl", bytes(pack_models.slot_bytes()))],
    ],
)
def test_bad_entries_are_refused(entries) -> None:
    with pytest.raises(ValueError):
        pack_models.pack(entries)
