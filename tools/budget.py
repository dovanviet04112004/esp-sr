#!/usr/bin/env python3
"""Merge the CSVs of the bench apps into the table of docs/measurements/budget.md (KEHOACH 4.8, TASKS E5-T13).

Rows come from docs/measurements/bench/*.csv as test_apps/bench_* write them. The frame share uses the hop of
contracts/grid.yaml and the core targets of ml/configs/common/hardware.yaml; rows with in_total 0 are parts of
another row and stay out of the core totals. With no CSV the table is written empty.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from tools.gen_contracts import grid_values

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = REPO_ROOT / "docs" / "measurements" / "bench"
BUDGET = REPO_ROOT / "docs" / "measurements" / "budget.md"
HARDWARE = REPO_ROOT / "ml" / "configs" / "common" / "hardware.yaml"
TABLE_HEAD = (
    "| Module | Nhân | RAM tĩnh B | Vùng `hot` B | Vùng `cold` B | µs trung bình | µs đỉnh | % khung 16 ms"
    " | Commit | Ngày |"
)
TOTAL_PREFIX = "Tổng nhân "
PERCENT = 100.0


@dataclass(frozen=True)
class Row:
    module: str
    core: int
    static_bytes: int
    hot_bytes: int
    cold_bytes: int
    us_mean: float
    us_peak: float
    in_total: bool
    fw: str
    date: str


def number(value: float) -> str:
    """One decimal with a comma, as the tables of docs/measurements write numbers."""
    return f"{value:.1f}".replace(".", ",")


def read_rows(bench_dir: Path) -> list[Row]:
    rows = []
    for path in sorted(bench_dir.glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                rows.append(
                    Row(
                        module=r["module"],
                        core=int(r["core"]),
                        static_bytes=int(r["static_bytes"]),
                        hot_bytes=int(r["hot_bytes"]),
                        cold_bytes=int(r["cold_bytes"]),
                        us_mean=float(r["us_mean"]),
                        us_peak=float(r["us_peak"]),
                        in_total=r["in_total"] == "1",
                        fw=r["fw"],
                        date=r["date"],
                    )
                )
    return sorted(rows, key=lambda r: (-r.core, r.module))


def table_lines(rows: list[Row], hop_us: float) -> list[str]:
    return [
        f"| {r.module} | {r.core} | {r.static_bytes} | {r.hot_bytes} | {r.cold_bytes} | {number(r.us_mean)}"
        f" | {number(r.us_peak)} | {number(PERCENT * r.us_mean / hop_us)} | `{r.fw}` | {r.date} |"
        for r in rows
    ]


def total_lines(rows: list[Row], hop_us: float, targets: dict) -> list[str]:
    """One line per core: the rows that count, their mean and peak share of a hop, against the targets."""
    lines = []
    for core, mean_key, peak_key in (
        (1, "core1_mean_load_max", "core1_peak_frame_max"),
        (0, "core0_mean_load_max", None),
    ):
        counted = [r for r in rows if r.core == core and r.in_total]
        mean_pct = PERCENT * sum(r.us_mean for r in counted) / hop_us
        peak_pct = PERCENT * sum(r.us_peak for r in counted) / hop_us
        goal = f"mục tiêu KẾ HOẠCH §5.6: trung bình ≤ {PERCENT * targets[mean_key]:.0f}%"
        if peak_key:
            goal += f", đỉnh một khung ≤ {PERCENT * targets[peak_key]:.0f}%"
        if not counted:
            lines.append(f"{TOTAL_PREFIX}{core}: chưa có số đo ({goal}).")
            continue
        lines.append(
            f"{TOTAL_PREFIX}{core}: trung bình {number(mean_pct)}%, đỉnh cộng dồn {number(peak_pct)}% một khung"
            f" ({goal}); gồm {', '.join(r.module for r in counted)}."
        )
    return lines


def render(text: str, rows: list[Row], hop_us: float, targets: dict) -> str:
    """budget.md with its table rows and core totals rewritten; every other line kept."""
    lines = text.splitlines()
    head = lines.index(TABLE_HEAD)
    end = head + 2
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    kept_after = [line for line in lines[end:] if not line.startswith(TOTAL_PREFIX)]
    while kept_after and not kept_after[0].strip():
        kept_after.pop(0)
    out = lines[: head + 2] + table_lines(rows, hop_us) + ["", *total_lines(rows, hop_us, targets)]
    if kept_after:
        out += ["", *kept_after]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail when budget.md is not what the CSVs give")
    args = parser.parse_args(argv)
    hop_us = float(grid_values()["hop_us"])
    targets = yaml.safe_load(HARDWARE.read_text(encoding="utf-8"))
    text = BUDGET.read_text(encoding="utf-8")
    fresh = render(text, read_rows(BENCH_DIR), hop_us, targets)
    if args.check:
        if fresh != text:
            print("budget.md is stale: run make measure", file=sys.stderr)
            return 1
        return 0
    if fresh != text:
        BUDGET.write_text(fresh, encoding="utf-8")
        print(f"wrote {BUDGET.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
