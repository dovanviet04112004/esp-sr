"""Run bench_afe on board B and keep its rows as docs/measurements/bench/bench_afe.csv (KEHOACH 4.5.7).

Rows arrive through test_report as "csv ..." lines, CRC-checked and asked for again when lost; tools/budget.py turns
the file into the table of docs/measurements/budget.md. "alt ..." lines, when an ADR needs a comparison, are printed
and not kept.
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_report import collect

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "docs" / "measurements" / "bench" / "bench_afe.csv"
COLUMNS = ("module", "core", "static_bytes", "hot_bytes", "cold_bytes", "us_mean", "us_peak", "in_total", "fw")
TAG = "BENCH"


def rows_of(texts: list[str]) -> list[dict[str, str]]:
    rows = []
    for text in texts:
        if text.startswith("csv "):
            values = next(csv.reader([text.removeprefix("csv ")]))
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
    texts = collect(dut, TAG)
    for text in texts:
        print(text)
    rows = rows_of(texts)
    assert rows, "the app reported no csv row"
    write_rows(rows)
