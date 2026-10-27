"""esp_ppq_patches.ops.matmul_s8: projections and their bias as MatMulS8, with esp-dl's integers."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from esp_ppq.executor.torch import TorchExecutor  # noqa: E402
from torch import nn  # noqa: E402

from esp_ppq_cases import HOPS, RUNGS, espdl_graph, exponent, round_shift  # noqa: E402
from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl  # noqa: E402


class Projections(nn.Module):
    """Each hop of 24 values through two projections with bias, as ReDimNet2's attention projects q, k and v."""

    def __init__(self) -> None:
        super().__init__()
        self.first, self.second = nn.Linear(24, 24), nn.Linear(24, 24)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.second(self.first(x))


def matmul_s8_espdl(rows: np.ndarray, laid: np.ndarray, bias: np.ndarray, shift: int) -> np.ndarray:
    """MatMulS8's int8 output for int8 rows [T, K] from its weights [Np / 16][K][16], bias [Np] and shift, as
    esp-dl's MatMul and the Add of its bias give it."""
    k, n = rows.shape[1], bias.size
    weights = laid.astype(np.int64).transpose(0, 2, 1).reshape(-1, k)[:n]
    return np.clip(round_shift(rows.astype(np.int64) @ weights.T, shift) + bias.astype(np.int64), -128, 127)


def test_matmul_s8_exports_each_projection_and_its_bias_as_one_op(tmp_path: Path) -> None:
    torch.manual_seed(4)
    rng = np.random.default_rng(23)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, HOPS, 24)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(Projections().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    with esp_ppq_patches.applied(["matmul_s8"]):
        path = export_espdl.export(graph, tmp_path / "projections.espdl")
    nodes, tensors = espdl_graph(path)
    assert [op for op, _ in nodes.values()].count("MatMulS8") == 2
    assert not {"MatMul", "Add"} & {op for op, _ in nodes.values()}
    info = path.with_suffix(".info").read_text()
    ((made, rows_name, w, b, shift),) = [
        (m.group(1), m.group(3), m.group(4), m.group(5), int(m.group(2)))
        for m in re.finditer(r"%(\S+) = MatMulS8\[.*shift = (\d+)\]\(%([^,]+), %([^,]+), %([^)]+)\)", info)
        if m.group(3) not in graph.inputs
    ]
    values = TorchExecutor(graph, device="cpu").forward(inputs=calib[0], output_names=[rows_name, made])
    rows, want = (
        torch.round(v / 2.0 ** exponent(info, name)).to(torch.int64).numpy().reshape(HOPS, 24)
        for v, name in zip(values, (rows_name, made), strict=True)
    )
    assert np.array_equal(matmul_s8_espdl(rows, tensors[w], tensors[b][:24], shift), want)
    assert not np.array_equal(matmul_s8_espdl(rows, tensors[w], tensors[b][:24], shift + 1), want)
