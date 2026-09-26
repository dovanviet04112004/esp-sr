"""Check the .gold writer and reader agree, reject damage, and match the C reader byte for byte."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from srpipe.golden import gold

REPO = Path(__file__).resolve().parents[2]
C_READER_DIR = REPO / "firmware" / "test_apps" / "parity" / "main"

C_DUMP = r"""
#include <stdio.h>
#include <stdlib.h>
#include "gold_read.h"
int main(int argc, char **argv)
{
    if (argc < 2) { return 2; }
    FILE *f = fopen(argv[1], "rb");
    static uint8_t buf[1 << 20];
    size_t n = fread(buf, 1, sizeof(buf), f);
    gold_reader_t r;
    gold_tensor_t t;
    if (!gold_open(&r, buf, n)) { return 3; }
    while (gold_next(&r, &t)) {
        printf("%s %d %u %u %u %u %u %u ", t.name, (int) t.dtype, t.ndim, t.dims[0], t.dims[1], t.dims[2],
               t.dims[3], t.nbytes);
        for (uint32_t i = 0; i < t.nbytes; i++) { printf("%02x", ((const uint8_t *) t.data)[i]); }
        printf("\n");
    }
    return r.remaining == 0 ? 0 : 4;
}
"""


def sample() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(1)
    return {
        "spectrum": rng.standard_normal((2, 257)).astype(np.float32),
        "weights": rng.integers(-128, 127, size=(3, 5, 7), dtype=np.int8),
        "index": np.arange(5, dtype=np.int32),
        "bytes": np.array([0, 255, 7], dtype=np.uint8),
        "pcm": rng.integers(-32768, 32767, size=(256, 2), dtype=np.int16),
        "scalar": np.array(3.25, dtype=np.float32),
    }


def test_round_trip_keeps_names_order_shapes_and_bits(tmp_path: Path) -> None:
    path = tmp_path / "case_000.gold"
    original = sample()
    gold.write_gold(path, original)
    back = gold.read_gold(path)
    assert list(back) == list(original)
    for name, value in original.items():
        assert back[name].dtype == value.dtype and back[name].shape == value.shape
        np.testing.assert_array_equal(back[name], value)


def test_wrong_magic_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "x.gold"
    gold.write_gold(path, {"a": np.zeros(3, np.float32)})
    path.write_bytes(b"GOLF" + path.read_bytes()[4:])
    with pytest.raises(ValueError):
        gold.read_gold(path)


def test_trailing_bytes_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "x.gold"
    gold.write_gold(path, {"a": np.zeros(3, np.float32)})
    path.write_bytes(path.read_bytes() + b"\0\0\0\0")
    with pytest.raises(ValueError):
        gold.read_gold(path)


def test_unsupported_dtype_and_long_name_are_refused(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        gold.write_gold(tmp_path / "x.gold", {"a": np.zeros(3, np.float64)})
    with pytest.raises(ValueError):
        gold.write_gold(tmp_path / "x.gold", {"n" * 32: np.zeros(3, np.float32)})


@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc")
def test_c_reader_sees_the_same_bytes(tmp_path: Path) -> None:
    (tmp_path / "dump.c").write_text(C_DUMP)
    exe = tmp_path / "dump"
    flags = ["-std=c11", "-Wall", "-Wextra", "-Werror", f"-I{C_READER_DIR}"]
    subprocess.run(["gcc", *flags, str(tmp_path / "dump.c"), "-o", str(exe)], check=True)
    path = tmp_path / "case.gold"
    original = sample()
    gold.write_gold(path, original)
    out = subprocess.run([str(exe), str(path)], capture_output=True, text=True, check=True).stdout.splitlines()
    assert len(out) == len(original)
    for line, (name, value) in zip(out, original.items(), strict=True):
        fields = line.split()
        dims = [*value.shape, *([0] * (4 - value.ndim))]
        assert fields[0] == name
        assert int(fields[1]) == gold.CODES[value.dtype]
        assert [int(x) for x in fields[2:7]] == [value.ndim, *dims]
        assert int(fields[7]) == value.nbytes
        assert fields[8] == value.astype(value.dtype.newbyteorder("<")).tobytes().hex()
