"""The plain C kernels against the PIE ones on the board, then the host's C build against the board (ADR-0045).

KERNELS_HOST names the host binary of test_apps/host, which runs the same seeded cases through the C kernels.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pytest_embedded_idf.dut import IdfDut

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "test_apps"))
from test_report import collect


def board_report(lines: list[str]) -> tuple[dict[tuple[str, int], str], list[str], int, dict[str, int]]:
    """The board's digest per case, the cases where C and PIE differ, how often the negative control differed, and
    how many float steps of the chip came apart from the standard C ones."""
    digests: dict[tuple[str, int], str] = {}
    differ: list[str] = []
    negative = 0
    floats: dict[str, int] = {}
    for text in lines:
        parts = text.split()
        if parts[0] == "kernel":
            _, kernel, index, digest, verdict = parts
            digests[(kernel, int(index))] = digest
            if verdict != "same":
                differ.append(f"{kernel} {index}")
        elif parts[0] == "negative":
            negative = int(parts[2])
        elif parts[0] == "float":
            floats[parts[1]] = int(parts[2])
    return digests, differ, negative, floats


def host_digests(binary: Path) -> dict[tuple[str, int], str]:
    """The host's digest per case."""
    out = subprocess.run([str(binary)], capture_output=True, text=True, check=True).stdout
    digests = {}
    for line in out.splitlines():
        _, kernel, index, digest = line.split()
        digests[(kernel, int(index))] = digest
    return digests


@pytest.mark.esp32s3
def test_kernels(dut: IdfDut) -> None:
    board, differ, negative, floats = board_report(collect(dut, "kernels"))
    assert board, "the board reported no case"
    assert not differ, f"C differs from PIE on the board: {differ}"
    assert negative > 0, "the negative control, half down, matched the PIE kernel"
    assert floats == {"divide": 0, "round": 0}, f"the chip's float steps came apart from standard C: {floats}"
    host = host_digests(Path(os.environ["KERNELS_HOST"]))
    mismatch = sorted(k for k in board.keys() | host.keys() if board.get(k) != host.get(k))
    assert not mismatch, f"the host's C differs from the board: {mismatch[:20]}"
