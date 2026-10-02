"""Rung 4: a quantised graph learns through its fake quantisation, on sequences of another length than it was traced
on, and carry moves what it learnt onto the graph of one batch, refused when a parameter is left behind; a graph
holding a fixed exponent as a parameter is refused before it learns."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from srpipe.compress.quant import ptq_espdl, qat_espdl  # noqa: E402
from srpipe.tasks.wake.model.tcn import Tcn  # noqa: E402

BANDS, HOPS, BATCH = 8, 40, 4
RUNGS = ptq_espdl.ladder("wake") | {"equalization": None, "bias_correction": False, "calibration": "minmax"}


def graphs(tmp_path: Path) -> tuple:
    """The graph of one TCN built for BATCH and for one, calibrated on the same sequences."""
    torch.manual_seed(0)
    model = Tcn(BANDS, 8, 3, [1, 2])
    rows = np.random.default_rng(0).normal(0, 1, (2 * BATCH, BANDS, HOPS)).astype(np.float32)
    batched = [torch.from_numpy(rows[k : k + BATCH]) for k in range(0, len(rows), BATCH)]
    ones = [torch.from_numpy(rows[k : k + 1]) for k in range(len(rows))]
    return ptq_espdl.quantize(model, batched, tmp_path / "b", RUNGS), ptq_espdl.quantize(
        model, ones, tmp_path / "1", RUNGS
    )


def trained(graph) -> None:
    """Thirty steps towards a fixed target on sequences shorter than the trace, the loss falling."""
    net = qat_espdl.Trainable(graph, "cpu")
    optimiser = torch.optim.Adam(net.parameters, lr=1e-2)
    rng = np.random.default_rng(1)
    x = torch.from_numpy(rng.normal(0, 1, (BATCH, BANDS, HOPS // 2)).astype(np.float32))
    target = torch.from_numpy(rng.normal(0, 1, (BATCH, 1, HOPS // 2)).astype(np.float32))
    losses = []
    for _ in range(30):
        loss = torch.mean((net(x) - target) ** 2)
        optimiser.zero_grad()
        loss.backward()
        optimiser.step()
        losses.append(loss.item())
    net.freeze()
    assert losses[-1] < 0.9 * losses[0]


def test_what_the_batch_graph_learnt_runs_on_the_graph_of_one(tmp_path: Path) -> None:
    wide, one = graphs(tmp_path)
    x = np.random.default_rng(2).normal(0, 1, (1, BANDS, HOPS)).astype(np.float32)
    before = ptq_espdl.Simulator(one)(x)
    trained(wide)
    qat_espdl.carry(wide, one, x)
    assert qat_espdl.batch_of(wide) == BATCH and not np.array_equal(ptq_espdl.Simulator(one)(x), before)


def test_a_parameter_left_behind_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wide, one = graphs(tmp_path)
    trained(wide)
    every = qat_espdl.float_parameters
    calls = []

    def short_of_one(graph) -> dict:
        found = every(graph)
        calls.append(graph)
        return dict(list(found.items())[1:]) if len(calls) == 2 else found

    monkeypatch.setattr(qat_espdl, "float_parameters", short_of_one)
    x = np.random.default_rng(3).normal(0, 1, (1, BANDS, HOPS)).astype(np.float32)
    with pytest.raises(ValueError, match="differs from the trained one"):
        qat_espdl.carry(wide, one, x)


class Squares(torch.nn.Module):
    """A convolution of its input's square: ONNX keeps the exponent as a constant of Pow."""

    def __init__(self) -> None:
        super().__init__()
        self.conv = torch.nn.Conv1d(BANDS, 2, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.conv(x.pow(2.0))


def test_a_fixed_exponent_is_refused_before_it_learns(tmp_path: Path) -> None:
    torch.manual_seed(4)
    rng = np.random.default_rng(4)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, BANDS, HOPS)).astype(np.float32)) for _ in range(4)]
    graph = ptq_espdl.quantize(Squares(), calib, tmp_path, RUNGS)
    with pytest.raises(ValueError, match="fixed exponent"):
        qat_espdl.Trainable(graph, "cpu")
