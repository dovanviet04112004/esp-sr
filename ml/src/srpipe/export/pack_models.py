"""The model slot image of KEHOACH 6.3: a 1 KB header, then each entry 64-byte aligned with its sha256.
Layout constants come from sys_storage/include/storage_format.h and the slot size from firmware/partitions.csv, so
ai_engine_load and this packer cannot disagree. --lock packs every file of contracts/models.lock.json held to its sha256
(KEHOACH 4.5.6), the command's listen hash and thresholds, the speaker's hash, a threshold for each count of
voiceprints and its noise edges and noisier bins' thresholds, and flags in flash a row's graphs deployed so.
Run: python -m srpipe.export.pack_models <out.bin> (--lock | <name>:<kind>:<file>...)"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
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
KINDS = {
    kind: storage_define(f"STORAGE_MODEL_KIND_{kind.upper()}") for kind in ("espdl", "norm", "units", "cohort", "front")
}
IN_FLASH = storage_define("STORAGE_MODEL_FLAG_IN_FLASH")
SPEAKERS = storage_define("APP_SPEAKERS_MAX", APP_EVENTS)
NOISE_EDGES = storage_define("STORAGE_SPK_NOISE_EDGES")
ENTRY = struct.Struct(f"<{NAME_BYTES}sII{SHA256_BYTES}sII")
_FIELDS = f"<IIIIIHHHHI{SPEAKERS}hH"
HEAD = struct.Struct(f"{_FIELDS}{FIELDS_BYTES - struct.calcsize(_FIELDS)}x")
NOISE = struct.Struct(f"<{NOISE_EDGES}f{SPEAKERS * NOISE_EDGES}h")  # after the entries (ADR-0024)
INT16_MIN, INT16_MAX = -32768, 32767


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
    speaker_noise_edges: tuple[float, ...] = ()
    speaker_noisy_milli_z: tuple[tuple[int, ...], ...] = ()


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


def checked_noise(edges: tuple[float, ...], noisy: tuple[tuple[int, ...], ...]) -> None:
    """Refuse noise edges and thresholds the header cannot hold or the chip would misread: both or neither, as many
    as the header has, edges finite, at least 0 and rising, thresholds in int16 (ADR-0024)."""
    if bool(edges) != bool(noisy):
        raise ValueError("noise edges and noisy thresholds come together, or neither does")
    if not edges:
        return
    if len(edges) != NOISE_EDGES or any(not math.isfinite(e) or e < 0 for e in edges) or list(edges) != sorted(edges):
        raise ValueError(f"noise edges {edges}: the header holds {NOISE_EDGES} finite, at least 0 and rising")
    if len(noisy) != SPEAKERS or any(len(row) != NOISE_EDGES for row in noisy):
        raise ValueError(f"noisy thresholds {noisy}: the header holds {SPEAKERS} rows of {NOISE_EDGES}")
    if any(not INT16_MIN <= t <= INT16_MAX for row in noisy for t in row):
        raise ValueError(f"noisy thresholds {noisy}: each an int16")


def pack(
    entries: list[Entry],
    grid_hash: int = grid.GRID_HASH,
    listen_hash: int = 0,
    thresholds: tuple[int, int, int] = (0, 0, 0),
    speaker_hash: int = 0,
    speaker_milli_z: tuple[int, ...] = (),
    speaker_cohort_top: int = 0,
    speaker_noise_edges: tuple[float, ...] = (),
    speaker_noisy_milli_z: tuple[tuple[int, ...], ...] = (),
) -> bytes:
    """The image bytes: header, then the entries in order, each at the next ALIGN_BYTES boundary; listen_hash is the
    GEN_LISTEN_HASH the entries' command learned on and thresholds its delta1, delta2 and delta3 in permille, 0
    without a command or a chosen value; speaker_hash is the GEN_SPEAKER_HASH the speaker was imported on,
    speaker_milli_z its threshold in thousandths of the AS-norm score with 1, 2 ... SPEAKERS voiceprints held, 0 for
    each it lacks, speaker_cohort_top the cohort rows AS-norm averages over, and the noise edges and thresholds of
    the noisier bins, 0 without them, so every window takes the quiet table (KEHOACH 6.3, ADR-0023, ADR-0024)."""
    if not 0 < len(entries) <= MAX_ENTRIES:
        raise ValueError(f"{len(entries)} entries, the header holds 1..{MAX_ENTRIES}")
    if len(speaker_milli_z) > SPEAKERS:
        raise ValueError(f"{len(speaker_milli_z)} speaker thresholds, the header holds {SPEAKERS}")
    if not 0 <= speaker_cohort_top <= 0xFFFF:
        raise ValueError(f"cohort top {speaker_cohort_top}, the header holds a u16")
    checked_noise(speaker_noise_edges, speaker_noisy_milli_z)
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
    edges = speaker_noise_edges or (0.0,) * NOISE_EDGES
    noisy = [t for row in speaker_noisy_milli_z for t in row] or [0] * (SPEAKERS * NOISE_EDGES)
    header = head + b"".join(table) + bytes((MAX_ENTRIES - len(table)) * ENTRY.size) + NOISE.pack(*edges, *noisy)
    image = header + bytes(HEADER_BYTES - len(header)) + bytes(body)
    if len(image) > slot_bytes():
        raise ValueError(f"image of {len(image)} bytes overflows the {slot_bytes()}-byte slot")
    return image


def locked() -> Locked:
    """Every file models.lock.json lists, in its order, as the image entry it names, a row's graphs flagged to stay in
    flash when it was deployed so; the listen hash and delta1..3 of the rows that record them, 0 otherwise; the
    speaker's hash, thresholds for 1 to SPEAKERS voiceprints, cohort top and noise fields, none without them. Refused
    when a file differs from its sha256, rows learned on two listen.yaml, two record thresholds or a speaker, or the
    speaker's row lacks SPEAKERS thresholds or a cohort top, or holds noise fields checked_noise refuses."""
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
    noise = next((r["threshold"].get("noise", {}) for r in rows.values() if "speaker_hash" in r), {})
    edges = tuple(float(e) for e in noise.get("edges", []))
    noisy = tuple(tuple(int(t) for t in row) for row in noise.get("milli_z", []))
    checked_noise(edges, noisy)
    speaker_hash, table, top = next(iter(speakers), (0, [], 0))
    if speaker_hash and not (isinstance(table, list) and len(table) == SPEAKERS and isinstance(top, int) and top > 0):
        raise ValueError(
            f"the speaker of {LOCK.name} holds threshold {table!r} and cohort top {top!r}, the header one in"
            f" thousandths of the AS-norm score for each of 1..{SPEAKERS} voiceprints and a top: make speaker-deploy"
            " again"
        )
    triple = next(iter(triples), (0, 0, 0))
    thresholds = tuple(int(t) for t in table)
    return Locked(entries, next(iter(listen_hashes), 0), triple, speaker_hash, thresholds, int(top or 0), edges, noisy)


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
            speaker_noise_edges=lock.speaker_noise_edges,
            speaker_noisy_milli_z=lock.speaker_noisy_milli_z,
        )
    )
    size = args.out.stat().st_size
    deltas = ", ".join(map(str, lock.thresholds))
    said = f"grid 0x{grid.GRID_HASH:08x}, listen 0x{lock.listen_hash:08x}, delta1..3 {deltas}"
    said += f", speaker 0x{lock.speaker_hash:08x} at {', '.join(map(str, lock.speaker_milli_z)) or 0} milli-z"
    said += f" over the cohort's top {lock.speaker_cohort_top}"
    said += f", noise edges {', '.join(f'{e:g}' for e in lock.speaker_noise_edges) or 'none'}"
    print(f"{args.out}: {len(entries)} entries, {size} bytes, {said}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
