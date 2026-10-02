"""Rung 3's ranking: a convolution with one output channel a thousand times the others, so one power-of-two scale
rounds the quiet channels the next convolution reads to zero, ranks first; each row takes the first k of the
ranking."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from torch import nn  # noqa: E402

from srpipe.compress.quant import mixed_espdl, ptq_espdl  # noqa: E402

BANDS, HOPS = 8, 40
RUNGS = ptq_espdl.ladder("wake") | {"equalization": None, "bias_correction": False, "calibration": "minmax"}


class LoudChannel(nn.Module):
    """A convolution whose first output channel is a thousand times the others, read by one that ignores it."""

    def __init__(self) -> None:
        super().__init__()
        self.loud = nn.Conv1d(BANDS, BANDS, 1)
        self.reader = nn.Conv1d(BANDS, 4, 1)
        with torch.no_grad():
            self.loud.weight[0] *= 1000.0
            self.loud.bias[0] *= 1000.0
            self.reader.weight[:, 0] = 0.0

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.reader(self.loud(x))


def test_the_convolution_one_scale_cannot_hold_ranks_first(tmp_path: Path) -> None:
    torch.manual_seed(0)
    rng = np.random.default_rng(0)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, BANDS, HOPS)).astype(np.float32)) for _ in range(4)]
    graph = ptq_espdl.quantize(LoudChannel(), calib, tmp_path, RUNGS)
    ranked = mixed_espdl.ranked_layers(graph, calib)
    assert [name for name, _ in ranked] == ["/loud/Conv", "/reader/Conv"]
    assert ranked[0][1] > 10 * ranked[1][1]


def test_each_row_takes_the_first_k_of_the_ranking() -> None:
    ranked = [("a", 0.3), ("b", 0.2), ("c", 0.1)]
    assert mixed_espdl.int16_rows(ranked, [1, 2]) == {"int16_top1": ["a"], "int16_top2": ["a", "b"]}
