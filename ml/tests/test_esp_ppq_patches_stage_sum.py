"""esp_ppq_patches.ops.stage_sum: chains of weighted stage maps as StageSum."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from torch import nn  # noqa: E402
from torch.nn import functional  # noqa: E402

from esp_ppq_cases import BANDS, RUNGS, export_info  # noqa: E402
from srpipe.compress.quant import ptq_espdl  # noqa: E402


class StageSums(nn.Module):
    """Three maps weighted per channel and summed into two outputs, the third at half the frames and repeated, as
    ReDimNet2's weigth1d sums its stages; shared, the first product is an output of its own too."""

    def __init__(self, shared: bool = False) -> None:
        super().__init__()
        self.shared = shared
        self.maps = nn.ModuleList(nn.Conv1d(BANDS * 4, BANDS * 4, 1) for _ in range(3))
        self.weights = nn.ModuleList(nn.Conv1d(BANDS * 4, BANDS * 4, 1, groups=BANDS * 4, bias=False) for _ in range(5))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y0 = self.maps[0](x)
        y1 = self.maps[1](torch.relu(y0))
        y2 = functional.interpolate(self.maps[2](torch.relu(y1))[:, :, ::2], scale_factor=2.0, mode="nearest")
        first = self.weights[0](y0)
        sums = [first + self.weights[1](y1), self.weights[2](y0) + self.weights[3](y1) + self.weights[4](y2)]
        return torch.cat([*sums, first] if self.shared else sums, dim=1)


def stage_sums_info(tmp_path: Path, shared: bool, patches: list[str]) -> str:
    torch.manual_seed(3)
    rng = np.random.default_rng(5)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, BANDS * 4, 16)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(StageSums(shared).eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    return export_info(graph, tmp_path / f"sums_{shared}_{len(patches)}.espdl", patches)


def test_stage_sum_exports_each_chain_of_weighted_maps_as_one_op(tmp_path: Path) -> None:
    info = stage_sums_info(tmp_path, False, ["channel_scales_as_mul", "stage_sum"])
    rows = sorted(re.findall(r"= StageSum\[.*rows = \[([\d, ]+)\]", info))
    assert rows == ["1, 1", "1, 1, 2"]
    assert not re.findall(r"= (?:Mul|Add|Resize)\[", info)
    assert len(re.findall(r"= Mul\[", stage_sums_info(tmp_path, False, ["channel_scales_as_mul"]))) == 5


def test_stage_sum_leaves_a_chain_whose_product_another_op_reads(tmp_path: Path) -> None:
    info = stage_sums_info(tmp_path, True, ["channel_scales_as_mul", "stage_sum"])
    assert re.findall(r"= StageSum\[.*rows = \[([\d, ]+)\]", info) == ["1, 1, 2"]
    assert len(re.findall(r"= Mul\[", info)) == 2
    assert len(re.findall(r"= Add\[", info)) == 1
