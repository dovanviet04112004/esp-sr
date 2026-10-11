"""The model slot image of KEHOACH 6.3: a 1 KB header, then each entry 64-byte aligned with its sha256.
Layout constants come from sys_storage/include/storage_format.h and the slot size from firmware/partitions.csv, so
ai_engine_load and this packer cannot disagree. --lock packs every file of contracts/models.lock.json held to its sha256
(KEHOACH 4.5.6), the command's listen hash and thresholds, the speaker's hash and a threshold for each count of
voiceprints, and flags in flash a row's graphs deployed so.
Run: python -m srpipe.export.pack_models <out.bin> (--lock | <name>:<kind>:<file>...)"""

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
APP_EVENTS = FIRMWARE / "components" / "common" / "include" / "app_events.h"
PARTITIONS = FIRMWARE / "partitions.csv"
UNIT_PARTITIONS = FIRMWARE / "test_apps" / "partitions_unit.csv"
LOCK = FIRMWARE.parent / "contracts" / "models.lock.json"
MODELS = FIRMWARE / "models"
ENTRY_TAIL = struct.Struct("<II")
FIELDS_BYTES = 64  # storage_model_header_t ahead of its entries


def storage_define(name: str, header: Path = FORMAT_HEADER) -> int:
    """The integer a #define or enumerator of storage_format.h, or of header, gives name."""
    text = header.read_text(encoding="utf-8")
    match = re.search(rf"^\s*(?:#define\s+{name}\s+|{name}\s*=\s*)(0x[0-9a-fA-F]+|\d+)u?\b", text, flags=re.MULTILINE)
    if match is None:
        raise KeyError(f"{name} is not in {header.name}")
    return int(match.group(1), 0)


MAGIC = storage_define("STORAGE_MODEL_MAGIC")
FORMAT_VER = storage_define("STORAGE_MODEL_FORMAT_VER")
MAX_ENTRIES = storage_define("STORAGE_MODEL_MAX_ENTRIES")
NAME_BYTES = storage_define("STORAGE_MODEL_NAME_BYTES")
HEADER_BYTES = storage_define("STORAGE_MODEL_HEADER_BYTES")
ALIGN_BYTES = storage_define("STORAGE_MODEL_ALIGN_BYTES")
SHA256_BYTES = storage_define("STORAGE_MODEL_SHA256_BYTES")
KINDS = {kind: storage_define(f"STORAGE_MODEL_KIND_{kind.upper()}") for kind in ("espdl", "norm", "units", "cohort")}
IN_FLASH = storage_define("STORAGE_MODEL_FLAG_IN_FLASH")
SPEAKERS = storage_define("APP_SPEAKERS_MAX", APP_EVENTS)
ENTRY = struct.Struct(f"<{NAME_BYTES}sII{SHA256_BYTES}sII")
_FIELDS = f"<IIIIIHHHHI{SPEAKERS}hH"
HEAD = struct.Struct(f"{_FIELDS}{FIELDS_BYTES - struct.calcsize(_FIELDS)}x")


@dataclass(frozen=True)
class Entry:
    name: str
    kind: str
    data: bytes
    flags: int = 0  # STORAGE_MODEL_FLAG_* of storage_format.h


@dataclass(frozen=True)
class Locked:
    """What contracts/models.lock.json packs into: the entries and the header's fields of command and speaker."""

    entries: list[Entry]
    listen_hash: int = 0
    thresholds: tuple[int, int, int] = (0, 0, 0)
    speaker_hash: int = 0
    speaker_milli_z: tuple[int, ...] = ()
    speaker_cohort_top: int = 0


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
    speaker_hash: int = 0,
    speaker_milli_z: tuple[int, ...] = (),
    speaker_cohort_top: int = 0,
) -> bytes:
    """The image bytes: header, then the entries in order, each at the next ALIGN_BYTES boundary; listen_hash is the
    GEN_LISTEN_HASH the entries' command learned on and thresholds its delta1, delta2 and delta3 in permille, 0
    without a command or a chosen value; speaker_hash is the GEN_SPEAKER_HASH the speaker was imported on,
    speaker_milli_z its threshold in thousandths of the AS-norm score with 1, 2 ... SPEAKERS voiceprints held, 0 for
    each it lacks, and speaker_cohort_top the cohort rows AS-norm averages over (KEHOACH 6.3, ADR-0023)."""
    if not 0 < len(entries) <= MAX_ENTRIES:
        raise ValueError(f"{len(entries)} entries, the header holds 1..{MAX_ENTRIES}")
    if len(speaker_milli_z) > SPEAKERS:
        raise ValueError(f"{len(speaker_milli_z)} speaker thresholds, the header holds {SPEAKERS}")
    if not 0 <= speaker_cohort_top <= 0xFFFF:
        raise ValueError(f"cohort top {speaker_cohort_top}, the header holds a u16")
    body, table, at = bytearray(), [], HEADER_BYTES
    for e in entries:
        name = e.name.encode("ascii")
        if len(name) > NAME_BYTES or not e.data or e.kind not in KINDS:
            raise ValueError(f"entry {e.name!r}: name over {NAME_BYTES} bytes, no data, or kind not in {sorted(KINDS)}")
        table.append(ENTRY.pack(name, at, len(e.data), hashlib.sha256(e.data).digest(), KINDS[e.kind], e.flags))
        padded = e.data + bytes(-len(e.data) % ALIGN_BYTES)
        body += padded
        at += len(padded)
    table_of = (*speaker_milli_z, *[0] * (SPEAKERS - len(speaker_milli_z)))
    head = HEAD.pack(
        MAGIC,
        FORMAT_VER,
        len(entries),
        grid_hash,
        listen_hash,
        *thresholds,
        0,
        speaker_hash,
        *table_of,
        speaker_cohort_top,
    )
    header = head + b"".join(table)
    image = header + bytes(HEADER_BYTES - len(header)) + bytes(body)
    if len(image) > slot_bytes():
        raise ValueError(f"image of {len(image)} bytes overflows the {slot_bytes()}-byte slot")
    return image


def locked() -> Locked:
    """Every file models.lock.json lists, in its order, as the image entry it names, a row's graphs flagged to stay in
    flash when it was deployed so; the listen hash of the rows that record one, 0 when none does, and the delta1,
    delta2 and delta3 of the row that records them, 0 for each it does not; the speaker hash, the thresholds for 1 to
    SPEAKERS voiceprints and the cohort's top of the row that records them. Refused when a file under
    firmware/models/<branch>/ differs from its sha256, two rows learned on different listen.yaml, two record thresholds
    or a speaker, or the speaker's row holds other than SPEAKERS thresholds or no cohort top."""
    rows = json.loads(LOCK.read_text(encoding="utf-8"))["models"]
    entries = []
    for branch, row in rows.items():
        for f in row["files"]:
            data = (MODELS / branch / f["file"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != f["sha256"]:
                raise ValueError(f"{branch}/{f['file']} differs from the sha256 {LOCK.name} holds")
            in_place = f["kind"] == "cohort" or (row.get("weights_in_flash") and f["kind"] == "espdl")
            flags = IN_FLASH if in_place else 0
            entries.append(Entry(f["entry"], f["kind"], data, flags))
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
    speakers = [
        (int(r["speaker_hash"], 16), r["threshold"].get("milli_z"), r.get("cohort", {}).get("top"))
        for r in rows.values()
        if "speaker_hash" in r
    ]
    if len(speakers) > 1:
        raise ValueError(f"{len(speakers)} rows of {LOCK.name} record a speaker, the header holds one")
    speaker_hash, table, top = next(iter(speakers), (0, [], 0))
    if speaker_hash and not (isinstance(table, list) and len(table) == SPEAKERS and isinstance(top, int) and top > 0):
        raise ValueError(
            f"the speaker of {LOCK.name} holds threshold {table!r} and cohort top {top!r}, the header one in"
            f" thousandths of the AS-norm score for each of 1..{SPEAKERS} voiceprints and a top: make speaker-deploy"
            " again"
        )
    triple = next(iter(triples), (0, 0, 0))
    thresholds = tuple(int(t) for t in table)
    return Locked(entries, next(iter(listen_hashes), 0), triple, speaker_hash, thresholds, int(top or 0))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("entries", nargs="*", help="<name>:<espdl|norm|units>:<file>; no listen hash, so no command")
    parser.add_argument("--lock", action="store_true", help="pack every file of contracts/models.lock.json")
    args = parser.parse_args(argv)
    if args.lock == bool(args.entries):
        parser.error("give either --lock or entries")
    lock = locked() if args.lock else Locked([])
    entries = list(lock.entries)
    for spec in args.entries:
        name, kind, path = spec.split(":", 2)
        entries.append(Entry(name, kind, Path(path).read_bytes()))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(
        pack(
            entries,
            listen_hash=lock.listen_hash,
            thresholds=lock.thresholds,
            speaker_hash=lock.speaker_hash,
            speaker_milli_z=lock.speaker_milli_z,
            speaker_cohort_top=lock.speaker_cohort_top,
        )
    )
    size = args.out.stat().st_size
    deltas = ", ".join(map(str, lock.thresholds))
    said = f"grid 0x{grid.GRID_HASH:08x}, listen 0x{lock.listen_hash:08x}, delta1..3 {deltas}"
    said += f", speaker 0x{lock.speaker_hash:08x} at {', '.join(map(str, lock.speaker_milli_z)) or 0} milli-z"
    said += f" over the cohort's top {lock.speaker_cohort_top}"
    print(f"{args.out}: {len(entries)} entries, {size} bytes, {said}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
