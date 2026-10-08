"""The model slot image of KEHOACH 6.3: a 1 KB header, then each entry 64-byte aligned with its sha256.

The layout constants are read from firmware/components/sys_storage/include/storage_format.h and the slot size from
firmware/partitions.csv, so ai_engine_load and this packer cannot disagree on either. --lock packs every file of
contracts/models.lock.json, each held to its sha256 (KEHOACH 4.5.6), with its command's listen hash and thresholds.
Run: python -m srpipe.export.pack_models <out.bin> (--lock | <name>:<espdl|norm|units>:<file>...)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from srpipe.core.config import ML_ROOT
from srpipe.generated import grid

FIRMWARE = ML_ROOT.parent / "firmware"
FORMAT_HEADER = FIRMWARE / "components" / "sys_storage" / "include" / "storage_format.h"
PARTITIONS = FIRMWARE / "partitions.csv"
UNIT_PARTITIONS = FIRMWARE / "test_apps" / "partitions_unit.csv"
LOCK = FIRMWARE.parent / "contracts" / "models.lock.json"
MODELS = FIRMWARE / "models"
HEAD = struct.Struct("<IIIIIHHH38x")
ENTRY_TAIL = struct.Struct("<II")


def storage_define(name: str) -> int:
    """The integer a #define or enumerator of storage_format.h gives name."""
    text = FORMAT_HEADER.read_text(encoding="utf-8")
    match = re.search(rf"^\s*(?:#define\s+{name}\s+|{name}\s*=\s*)(0x[0-9a-fA-F]+|\d+)u?\b", text, flags=re.MULTILINE)
    if match is None:
        raise KeyError(f"{name} is not in {FORMAT_HEADER.name}")
    return int(match.group(1), 0)


MAGIC = storage_define("STORAGE_MODEL_MAGIC")
FORMAT_VER = storage_define("STORAGE_MODEL_FORMAT_VER")
MAX_ENTRIES = storage_define("STORAGE_MODEL_MAX_ENTRIES")
NAME_BYTES = storage_define("STORAGE_MODEL_NAME_BYTES")
HEADER_BYTES = storage_define("STORAGE_MODEL_HEADER_BYTES")
ALIGN_BYTES = storage_define("STORAGE_MODEL_ALIGN_BYTES")
SHA256_BYTES = storage_define("STORAGE_MODEL_SHA256_BYTES")
KINDS = {kind: storage_define(f"STORAGE_MODEL_KIND_{kind.upper()}") for kind in ("espdl", "norm", "units")}
ENTRY = struct.Struct(f"<{NAME_BYTES}sII{SHA256_BYTES}sII")


@dataclass(frozen=True)
class Entry:
    name: str
    kind: str
    data: bytes


def storage_string(name: str) -> str:
    """The string a #define of storage_format.h gives name."""
    text = FORMAT_HEADER.read_text(encoding="utf-8")
    match = re.search(rf'^#define\s+{name}\s+"([^"]*)"', text, flags=re.MULTILINE)
    if match is None:
        raise KeyError(f"{name} is not in {FORMAT_HEADER.name}")
    return match.group(1)


def partition_bytes(label: str, table: Path = PARTITIONS) -> int:
    """Size of the partition of that label in a partition table, the product's unless told."""
    with table.open(encoding="utf-8") as f:
        rows = [[c.strip() for c in row] for row in csv.reader(f) if row and not row[0].lstrip().startswith("#")]
    return next(int(row[4], 0) for row in rows if row[0] == label)


def slot_bytes() -> int:
    """Size of models_0, the one model partition of partitions.csv (KEHOACH 6.1)."""
    return partition_bytes(storage_string("STORAGE_MODEL_LABEL_SLOT0"))


def pack(
    entries: list[Entry],
    grid_hash: int = grid.GRID_HASH,
    listen_hash: int = 0,
    thresholds: tuple[int, int, int] = (0, 0, 0),
) -> bytes:
    """The image bytes: header, then the entries in order, each at the next ALIGN_BYTES boundary; listen_hash is the
    GEN_LISTEN_HASH the entries' command learned on and thresholds its delta1, delta2 and delta3 in permille, 0
    without a command or a chosen value (KEHOACH 6.3)."""
    if not 0 < len(entries) <= MAX_ENTRIES:
        raise ValueError(f"{len(entries)} entries, the header holds 1..{MAX_ENTRIES}")
    body, table, at = bytearray(), [], HEADER_BYTES
    for e in entries:
        name = e.name.encode("ascii")
        if len(name) > NAME_BYTES or not e.data or e.kind not in KINDS:
            raise ValueError(f"entry {e.name!r}: name over {NAME_BYTES} bytes, no data, or kind not in {sorted(KINDS)}")
        table.append(ENTRY.pack(name, at, len(e.data), hashlib.sha256(e.data).digest(), KINDS[e.kind], 0))
        padded = e.data + bytes(-len(e.data) % ALIGN_BYTES)
        body += padded
        at += len(padded)
    header = HEAD.pack(MAGIC, FORMAT_VER, len(entries), grid_hash, listen_hash, *thresholds) + b"".join(table)
    image = header + bytes(HEADER_BYTES - len(header)) + bytes(body)
    if len(image) > slot_bytes():
        raise ValueError(f"image of {len(image)} bytes overflows the {slot_bytes()}-byte slot")
    return image


def locked() -> tuple[list[Entry], int, tuple[int, int, int]]:
    """Every file models.lock.json lists, in its order, as the image entry it names, the listen hash of the rows that
    record one, 0 when none does, and the delta1, delta2 and delta3 of the row that records them, 0 for each it does
    not; refused when a file under firmware/models/<branch>/ differs from its sha256, two rows learned on different
    listen.yaml or two record thresholds."""
    rows = json.loads(LOCK.read_text(encoding="utf-8"))["models"]
    entries = []
    for branch, row in rows.items():
        for f in row["files"]:
            data = (MODELS / branch / f["file"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != f["sha256"]:
                raise ValueError(f"{branch}/{f['file']} differs from the sha256 {LOCK.name} holds")
            entries.append(Entry(f["entry"], f["kind"], data))
    listen_hashes = {int(row["listen_hash"], 16) for row in rows.values() if "listen_hash" in row}
    if len(listen_hashes) > 1:
        raise ValueError(f"rows of {LOCK.name} learned on {len(listen_hashes)} listen.yaml, the header holds one")
    triples = [
        tuple(r["thresholds"].get(k, 0) for k in ("reject_permille", "margin_permille", "syllable_permille"))
        for r in rows.values()
        if "thresholds" in r
    ]
    if len(triples) > 1:
        raise ValueError(f"{len(triples)} rows of {LOCK.name} record thresholds, the header holds one command's")
    return entries, next(iter(listen_hashes), 0), next(iter(triples), (0, 0, 0))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("entries", nargs="*", help="<name>:<espdl|norm|units>:<file>; no listen hash, so no command")
    parser.add_argument("--lock", action="store_true", help="pack every file of contracts/models.lock.json")
    args = parser.parse_args(argv)
    if args.lock == bool(args.entries):
        parser.error("give either --lock or entries")
    entries, listen_hash, thresholds = locked() if args.lock else ([], 0, (0, 0, 0))
    for spec in args.entries:
        name, kind, path = spec.split(":", 2)
        entries.append(Entry(name, kind, Path(path).read_bytes()))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(pack(entries, listen_hash=listen_hash, thresholds=thresholds))
    size = args.out.stat().st_size
    said = f"grid 0x{grid.GRID_HASH:08x}, listen 0x{listen_hash:08x}, delta1..3 {', '.join(map(str, thresholds))}"
    print(f"{args.out}: {len(entries)} entries, {size} bytes, {said}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
