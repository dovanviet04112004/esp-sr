"""torch → ONNX as ESP-PPQ reads it: onnxruntime gives what torch gives, the tensors carry the names asked for, and a
network whose trace leaves torch's path, as a branch on the data does, is refused."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
onnx = pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")

from torch import nn  # noqa: E402

from srpipe.compress.quant import onnx_export  # noqa: E402

BANDS, HOPS = 8, 40


class BranchesOnData(nn.Module):
    """Doubles its input when the input sums above zero: the trace on zeros keeps only the other path."""

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x * 2.0 if float(x.sum()) > 0 else x


def test_onnxruntime_gives_what_torch_gives_under_the_names_asked_for(tmp_path: Path) -> None:
    torch.manual_seed(0)
    model = nn.Sequential(nn.Conv1d(BANDS, 4, 3), nn.ReLU(), nn.Conv1d(4, 2, 1))
    x = np.random.default_rng(1).normal(0, 1, (1, BANDS, HOPS)).astype(np.float32)
    out = onnx_export.checked(model, (x,), tmp_path / "net.onnx", 1e-6, ["features"], ["logits"])
    graph = onnx.load(str(out)).graph
    assert [i.name for i in graph.input] == ["features"] and [o.name for o in graph.output] == ["logits"]
    assert onnx_export.gap(model, out, (x,)) <= 1e-6


def test_a_trace_that_leaves_torchs_path_is_refused(tmp_path: Path) -> None:
    x = np.abs(np.random.default_rng(2).normal(0, 1, (1, BANDS, HOPS))).astype(np.float32)
    with pytest.warns(torch.jit.TracerWarning), pytest.raises(ValueError, match="differs from torch"):
        onnx_export.checked(BranchesOnData(), (x,), tmp_path / "net.onnx", 1e-6)
