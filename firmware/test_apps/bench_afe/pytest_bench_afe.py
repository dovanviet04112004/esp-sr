"""Run bench_afe on board B and keep its rows as docs/measurements/bench/bench_afe.csv (KEHOACH 4.5.7).

tools/budget.py turns that file into the table of docs/measurements/budget.md; BENCH_ALT lines are comparisons
for an ADR and are printed, not kept.
"""

from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "docs" / "measurements" / "bench" / "bench_afe.csv"
COLUMNS = ("module", "core", "static_bytes", "hot_bytes", "cold_bytes", "us_mean", "us_peak", "in_total", "fw")
LINE = re.compile(rb"BENCH(?:_CSV|_ALT| done)[^\r\n]*(?=\r?\n)")


def rows_of(lines: list[str]) -> list[dict[str, str]]:
    rows = []
    for line in lines:
        if line.startswith("BENCH_CSV "):
            values = next(csv.reader([line.removeprefix("BENCH_CSV ")]))
            rows.append({**dict(zip(COLUMNS, values, strict=True)), "date": date.today().isoformat()})
    return rows


def write_rows(rows: list[dict[str, str]], out: Path = OUT) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[*COLUMNS, "date"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


@pytest.mark.esp32s3
def test_bench_afe(dut) -> None:
    lines: list[str] = []
    while not lines or lines[-1] != "BENCH done":
        lines.append(dut.expect(LINE, timeout=120).group(0).decode())
    for line in lines:
        print(line)
    rows = rows_of(lines)
    assert rows, "the app printed no BENCH_CSV row"
    write_rows(rows)
