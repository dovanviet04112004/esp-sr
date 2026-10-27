"""esp_ppq_patches.ops.weighted_stats: the pool's weighted sums over time as WeightedStats."""

from __future__ import annotations

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


class AttentivePool(nn.Module):
    """Weights over time from a convolution, then each channel's weighted sums of x and of x * x, as ReDimNet2's pool
    sums them."""

    def __init__(self) -> None:
        super().__init__()
        self.front, self.score = nn.Conv1d(16, 16, 1), nn.Conv1d(16, 16, 1)

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        x = self.front(inputs)
        p = torch.softmax(self.score(x), dim=2)
        return torch.cat([torch.sum(p * x, dim=2), torch.sum(p * (x * x), dim=2)], dim=1)


def test_weighted_stats_sums_both_products_over_time_as_one_op(tmp_path: Path) -> None:
    from esp_ppq.quantization.qfunction.linear import PPQLinearQuant_toInt

    torch.manual_seed(6)
    rng = np.random.default_rng(25)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 16, HOPS)).astype(np.float32)) for _ in range(2)]
    rungs = RUNGS | {"calibration": "minmax", "bias_correction": False}
    graph = ptq_espdl.quantize(AttentivePool().eval(), calib, tmp_path, rungs)
    with esp_ppq_patches.applied(["lean_quantizers", "softmax_quant", "weighted_stats"]):
        path = export_espdl.export(graph, tmp_path / "pool.espdl")
    ops = [op for op, _ in espdl_graph(path)[0].values()]
    assert ops.count("WeightedStats") == 1 and not {"Mul", "ReduceSum"} & set(ops)
    info = path.with_suffix(".info").read_text()
    shifts = [int(v) for v in re.search(r"= WeightedStats\[.*shifts = \[([-\d, ]+)\]", info).group(1).split(",")]

    (weigh,) = [
        op
        for op in graph.operations.values()
        if op.type == "Mul"
        and op.inputs[0] is not op.inputs[1]
        and not any(v.source_op is not None and v.source_op.type == "Mul" for v in op.inputs)
    ]
    (total,) = weigh.outputs[0].dest_ops
    (square_total,) = [op for op in graph.operations.values() if op.type == "ReduceSum" and op is not total]
    p, x = weigh.inputs if weigh.inputs[0].source_op.type == "Softmax" else weigh.inputs[::-1]
    names = [p.name, x.name, total.outputs[0].name, square_total.outputs[0].name]
    values = TorchExecutor(graph, device="cpu").forward(inputs=calib[0], output_names=names)
    configs = [weigh.input_quant_config[weigh.inputs.index(v)] for v in (p, x)]
    configs += [total.output_quant_config[0], square_total.output_quant_config[0]]
    ints = [
        PPQLinearQuant_toInt(v, c).to(torch.int64).numpy().reshape(-1) for v, c in zip(values, configs, strict=True)
    ]
    pi, xi = ints[0].reshape(16, HOPS), ints[1].reshape(16, HOPS)

    def stats(shift: list[int]) -> tuple[np.ndarray, np.ndarray]:
        square = round_shift(xi * xi, shift[0])
        weighted = round_shift(pi * xi, shift[1]).sum(axis=1)
        squared = round_shift(pi * square, shift[2]).sum(axis=1)
        return round_shift(weighted, shift[3]), round_shift(squared, shift[4])

    assert [np.array_equal(a, b) for a, b in zip(stats(shifts), ints[2:], strict=True)] == [True, True]
    off = stats([*shifts[:2], shifts[2] + 1, *shifts[3:]])
    assert not np.array_equal(off[1], ints[3])
