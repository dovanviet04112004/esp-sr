"""esp_ppq_patches.ops.global_stats: the pool's mean and centred squares over time as GlobalStats."""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from esp_ppq.executor.torch import TorchExecutor  # noqa: E402
from torch import nn  # noqa: E402

from esp_ppq_cases import HOPS, RUNGS, espdl_graph, round_shift  # noqa: E402
from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl  # noqa: E402


class PoolMoments(nn.Module):
    """A convolution's map, its mean over time and the mean of its centred squares, as ReDimNet2's pool takes its
    global context; no bias, so the means stay small beside the map and the difference on its grid, as in b0."""

    def __init__(self) -> None:
        super().__init__()
        self.front = nn.Conv1d(16, 16, 1, bias=False)

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        x = self.front(inputs)
        mean = x.mean(dim=2, keepdim=True)
        centred = x - mean
        return torch.cat([mean, (centred * centred).mean(dim=2, keepdim=True)], dim=2)


def mean_like_espdl(sums: np.ndarray, count: int, e_in: int, e_out: int) -> np.ndarray:
    """esp-dl's int8 ReduceMean of int32 sums: float32 sum * 2^e_in / count * 2^-e_out, floor of it plus 0.5."""
    v = sums.astype(np.float32) * np.float32(2.0**e_in) / np.float32(count) * np.float32(2.0**-e_out)
    return np.clip(np.floor(v + np.float32(0.5)), -128, 127).astype(np.int64)


def test_global_stats_takes_the_mean_and_centred_squares_as_one_op(tmp_path: Path) -> None:
    from esp_ppq.quantization.qfunction.linear import PPQLinearQuant_toInt

    torch.manual_seed(7)
    rng = np.random.default_rng(26)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 16, HOPS)).astype(np.float32)) for _ in range(2)]
    rungs = RUNGS | {"calibration": "minmax", "bias_correction": False}
    graph = ptq_espdl.quantize(PoolMoments().eval(), calib, tmp_path, rungs)
    with esp_ppq_patches.applied(["global_stats"]):
        path = export_espdl.export(graph, tmp_path / "moments.espdl")
    ops = [op for op, _ in espdl_graph(path)[0].values()]
    assert ops.count("GlobalStats") == 1 and not {"ReduceMean", "Sub", "Mul"} & set(ops)
    info = path.with_suffix(".info").read_text()
    found = re.search(r"= GlobalStats\[.*shifts = \[([-\d, ]+)\], square_exponent = (-?\d+)", info)
    requantise, square_shift = (int(v) for v in found.group(1).split(","))
    e_square = int(found.group(2))

    means = [op for op in graph.topological_sort() if op.type == "ReduceMean"]
    x = means[0].inputs[0]
    names = [x.name, means[0].outputs[0].name, means[1].outputs[0].name]
    values = TorchExecutor(graph, device="cpu").forward(inputs=calib[0], output_names=names)
    configs = [means[0].input_quant_config[0], means[0].output_quant_config[0], means[1].output_quant_config[0]]
    xi, want_mean, want_spread = (
        PPQLinearQuant_toInt(v, c).to(torch.int64).numpy().reshape(16, -1) for v, c in zip(values, configs, strict=True)
    )
    e_x, e_mean, e_spread = (round(math.log2(float(c.scale))) for c in configs)

    def moments(shift: int) -> tuple[np.ndarray, np.ndarray]:
        mean = mean_like_espdl(xi.sum(axis=1, keepdims=True), HOPS, e_x, e_mean)
        centre = round_shift(mean, requantise)
        centred = np.clip(xi - centre, -128, 127)
        squares = round_shift(centred * centred, shift)
        return mean, mean_like_espdl(squares.sum(axis=1, keepdims=True), HOPS, e_square, e_spread)

    got = moments(square_shift)
    assert np.array_equal(got[0], want_mean) and np.array_equal(got[1], want_spread)
    assert not np.array_equal(moments(square_shift + 1)[1], want_spread)
