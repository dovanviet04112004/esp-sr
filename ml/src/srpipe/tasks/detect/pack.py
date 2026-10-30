"""The vision image of KEHOACH 6.3 for the vision_0 slot: a 4 KB header, then the .espdl. Layout constants come from
sys_storage/include/storage_format.h and the slot size from firmware/partitions.csv, as pack_models reads them, so the
chip and this packer cannot disagree. Every field comes from the lock's detect row, held to its sha256. Writes
<out>/vision.bin, and its header and .espdl apart for make vision-flash, which writes the header last.
Run: python -m srpipe.tasks.detect.pack <out dir>"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from srpipe.export import pack_models

BRANCH = "detect"
MAGIC = pack_models.storage_define("STORAGE_VISION_MAGIC")
FORMAT_VER = pack_models.storage_define("STORAGE_VISION_FORMAT_VER")
HEADER_BYTES = pack_models.storage_define("STORAGE_VISION_HEADER_BYTES")
VERSION_BYTES = pack_models.storage_define("STORAGE_VISION_VERSION_BYTES")
MAX_CLASSES = pack_models.storage_define("STORAGE_VISION_MAX_CLASSES")
CLASS_BYTES = pack_models.storage_define("STORAGE_VISION_CLASS_BYTES")
LABEL = pack_models.storage_string("STORAGE_VISION_LABEL")
HEAD = struct.Struct(f"<II{VERSION_BYTES}sHHHHII{pack_models.SHA256_BYTES}s40x")


def image(espdl: bytes, version: str, size: int, names: list[str], score_permille: int, top_k: int) -> bytes:
    """The header, then espdl; refused when a field does not fit its place in the header, or the image the slot."""
    encoded = [n.encode("utf-8") for n in names]
    tag = version.encode("utf-8")
    if len(tag) >= VERSION_BYTES or not 0 < len(names) <= MAX_CLASSES or any(len(n) >= CLASS_BYTES for n in encoded):
        raise ValueError(f"version under {VERSION_BYTES} bytes, 1..{MAX_CLASSES} names under {CLASS_BYTES} bytes")
    if size % 32 or not 0 < score_permille <= 1000 or top_k < 1:
        raise ValueError(f"S {size} a multiple of 32, score {score_permille} in 1..1000 permille, top_k {top_k} >= 1")
    head = HEAD.pack(
        MAGIC,
        FORMAT_VER,
        tag,
        size,
        len(names),
        score_permille,
        top_k,
        HEADER_BYTES,
        len(espdl),
        hashlib.sha256(espdl).digest(),
    )
    table = b"".join(n.ljust(CLASS_BYTES, b"\0") for n in encoded)
    out = (head + table).ljust(HEADER_BYTES, b"\0") + espdl
    if len(out) > pack_models.partition_bytes(LABEL):
        raise ValueError(f"the image's {len(out)} bytes overflow {LABEL}")
    return out


def locked() -> bytes:
    """The vision image of the lock's detect row: its .espdl held to the row's sha256, its run as the version."""
    row = json.loads(pack_models.LOCK.read_text(encoding="utf-8"))["models"][BRANCH]
    (entry,) = row["files"]
    espdl = (pack_models.MODELS / BRANCH / entry["file"]).read_bytes()
    if hashlib.sha256(espdl).hexdigest() != entry["sha256"]:
        raise ValueError(f"{BRANCH}/{entry['file']} differs from the sha256 {pack_models.LOCK.name} holds")
    chip = row["chip"]
    return image(espdl, row["run"], row["size"], row["names"], chip["score_permille"], chip["top_k"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path, help="folder for vision.bin, vision.header.bin and vision.espdl.bin")
    args = parser.parse_args(argv)
    data = locked()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "vision.bin").write_bytes(data)
    (args.out / "vision.header.bin").write_bytes(data[:HEADER_BYTES])
    (args.out / "vision.espdl.bin").write_bytes(data[HEADER_BYTES:])
    print(f"{args.out / 'vision.bin'}: {len(data)} bytes for {LABEL}, header {HEADER_BYTES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
