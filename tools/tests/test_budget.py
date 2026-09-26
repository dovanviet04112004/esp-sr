"""budget.py writes the bench rows into budget.md, shares of a hop from the grid, totals only from counted rows."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools import budget

TARGETS = {"core1_mean_load_max": 0.5, "core1_peak_frame_max": 0.8, "core0_mean_load_max": 0.7}
HOP_US = 16000.0
CSV = """module,core,static_bytes,hot_bytes,cold_bytes,us_mean,us_peak,in_total,fw,date
chain,1,6200,40000,0,800.0,1200.0,1,v1-2-gabc,2026-09-26
stft part,1,6200,8000,0,300.0,400.0,0,v1-2-gabc,2026-09-26
"""


class BudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bench = Path(self.tmp.name)
        self.page = budget.BUDGET.read_text(encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_no_csv_gives_an_empty_table_and_no_total(self) -> None:
        out = budget.render(self.page, budget.read_rows(self.bench), HOP_US, TARGETS)
        rows_after_head = out.split(budget.TABLE_HEAD)[1].splitlines()[2]
        self.assertEqual(rows_after_head, "")
        self.assertIn("Tổng nhân 1: chưa có số đo", out)

    def test_rows_get_their_frame_share_and_only_counted_rows_make_the_total(self) -> None:
        (self.bench / "bench_afe.csv").write_text(CSV, encoding="utf-8")
        out = budget.render(self.page, budget.read_rows(self.bench), HOP_US, TARGETS)
        self.assertIn("| chain | 1 | 6200 | 40000 | 0 | 800,0 | 1200,0 | 5,0 | `v1-2-gabc` | 2026-09-26 |", out)
        self.assertIn("| stft part | 1 |", out)
        self.assertIn("Tổng nhân 1: trung bình 5,0%, đỉnh cộng dồn 7,5% một khung", out)
        self.assertIn("gồm chain.", out)

    def test_rendering_twice_changes_nothing(self) -> None:
        (self.bench / "bench_afe.csv").write_text(CSV, encoding="utf-8")
        rows = budget.read_rows(self.bench)
        once = budget.render(self.page, rows, HOP_US, TARGETS)
        self.assertEqual(budget.render(once, rows, HOP_US, TARGETS), once)
