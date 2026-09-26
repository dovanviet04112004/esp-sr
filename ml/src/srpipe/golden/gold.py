"""Write and read .gold files: a flat little-endian container of named tensors (KEHOACH 4.2).

Layout: magic "GOLD", version u32, count u32, then per tensor a 32-byte NUL-padded name, dtype u32,
ndim u32, four u32 dims, nbytes u32 and the data padded to a multiple of four bytes. The C reader in
firmware/test_apps/parity/main/gold_read.h parses exactly this.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

MAGIC = b"GOLD"
VERSION = 1
NAME_BYTES = 32
MAX_DIMS = 4
DTYPES = {0: np.float32, 1: np.int8, 2: np.int32, 3: np.uint8, 4: np.int16}
CODES = {np.dtype(v): k for k, v in DTYPES.items()}
HEADER = struct.Struct("<4sII")
RECORD = struct.Struct(f"<{NAME_BYTES}sII{MAX_DIMS}II")


def write_gold(path: Path, tensors: dict[str, np.ndarray]) -> None:
    """Write tensors in insertion order; names are ASCII and shorter than 32 bytes."""
    out = bytearray(HEADER.pack(MAGIC, VERSION, len(tensors)))
    for name, value in tensors.items():
        array = np.asarray(value, order="C")
        if array.dtype not in CODES:
            raise TypeError(f"{name}: dtype {array.dtype} has no .gold code")
        if array.ndim > MAX_DIMS:
            raise ValueError(f"{name}: {array.ndim} dims, at most {MAX_DIMS}")
        encoded = name.encode("ascii")
        if len(encoded) >= NAME_BYTES:
            raise ValueError(f"{name}: name longer than {NAME_BYTES - 1} bytes")
        dims = [*array.shape, *([0] * (MAX_DIMS - array.ndim))]
        data = array.astype(array.dtype.newbyteorder("<"), copy=False).tobytes()
        out += RECORD.pack(encoded, CODES[array.dtype], array.ndim, *dims, len(data))
        out += data + b"\0" * (-len(data) % 4)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))


def read_gold(path: Path) -> dict[str, np.ndarray]:
    raw = path.read_bytes()
    magic, version, count = HEADER.unpack_from(raw, 0)
    if magic != MAGIC or version != VERSION:
        raise ValueError(f"{path}: not a version {VERSION} .gold file")
    offset, tensors = HEADER.size, {}
    for _ in range(count):
        name, code, ndim, *rest = RECORD.unpack_from(raw, offset)
        dims, nbytes = rest[:MAX_DIMS], rest[MAX_DIMS]
        offset += RECORD.size
        dtype = np.dtype(DTYPES[code]).newbyteorder("<")
        array = np.frombuffer(raw, dtype=dtype, count=nbytes // dtype.itemsize, offset=offset)
        tensors[name.rstrip(b"\0").decode("ascii")] = array.reshape(dims[:ndim]).astype(DTYPES[code])
        offset += nbytes + (-nbytes % 4)
    if offset != len(raw):
        raise ValueError(f"{path}: {len(raw) - offset} trailing bytes")
    return tensors
